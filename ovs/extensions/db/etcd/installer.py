# Copyright 2015 iNuron NV
#
# Licensed under the Open vStorage Modified Apache License (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.openvstorage.org/license
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import time
from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.system import System
from ovs.extensions.services.service import ServiceManager
from ovs.log.logHandler import LogHandler

logger = LogHandler.get('extensions', name='etcd_installer')


class EtcdInstaller(object):
    """
    class to dynamically install/(re)configure etcd cluster
    """
    DB_DIR = '/opt/OpenvStorage/db'
    DATA_DIR = '{0}/etcd/{1}/data'
    WAL_DIR = '{0}/etcd/{1}/wal'
    SERVER_URL = 'http://{0}:2380'
    CLIENT_URL = 'http://{0}:2379'
    MEMBER_REGEX = re.compile(ur'^(?P<id>[^:]+): name=(?P<name>[^ ]+) peerURLs=(?P<peer>[^ ]+) clientURLs=(?P<client>[^ ]+)$')

    def __init__(self):
        """
        EtcdInstaller should not be instantiated
        """
        raise RuntimeError('EtcdInstaller is a complete static helper class')

    @staticmethod
    def create_cluster(cluster_name, ip):
        """
        Creates a cluster
        :param ip: IP address of the first node of the new cluster
        :param cluster_name: Name of the cluster
        """
        logger.debug('Creating cluster "{0}" on {1}'.format(cluster_name, ip))

        client = SSHClient(ip, username='root')
        node_name = System.get_my_machine_id(client)

        data_dir = EtcdInstaller.DATA_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        wal_dir = EtcdInstaller.WAL_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        abs_paths = [data_dir, wal_dir]
        client.dir_delete(abs_paths)
        client.dir_create(abs_paths)
        client.dir_chmod(abs_paths, 0755, recursive=True)
        client.dir_chown(abs_paths, 'ovs', 'ovs', recursive=True)

        base_name = 'ovs-etcd'
        target_name = 'ovs-etcd-{0}'.format(cluster_name)
        ServiceManager.add_service(base_name, client,
                                   params={'CLUSTER': cluster_name,
                                           'NODE_ID': node_name,
                                           'DATA_DIR': data_dir,
                                           'WAL_DIR': wal_dir,
                                           'SERVER_URL': EtcdInstaller.SERVER_URL.format(ip),
                                           'CLIENT_URL': EtcdInstaller.CLIENT_URL.format(ip),
                                           'LOCAL_CLIENT_URL': EtcdInstaller.CLIENT_URL.format('127.0.0.1'),
                                           'INITIAL_CLUSTER': '{0}={1}'.format(node_name, EtcdInstaller.SERVER_URL.format(ip)),
                                           'INITIAL_STATE': 'new',
                                           'INITIAL_PEERS': '-initial-advertise-peer-urls {0}'.format(EtcdInstaller.SERVER_URL.format(ip))},
                                   target_name=target_name)
        EtcdInstaller.start(cluster_name, client)
        EtcdInstaller.wait_for_cluster(cluster_name, client)

        logger.debug('Creating cluster "{0}" on {1} completed'.format(cluster_name, ip))

    @staticmethod
    def extend_cluster(master_ip, new_ip, cluster_name):
        """
        Extends a cluster to a given new node
        :param cluster_name: Name of the cluster to be extended
        :param new_ip: IP address of the node to be added
        :param master_ip: IP of one of the already existing nodes
        """
        logger.debug('Extending cluster "{0}" from {1} to {2}'.format(cluster_name, master_ip, new_ip))

        client = SSHClient(master_ip, username='root')
        if not EtcdInstaller._is_healty(cluster_name, client):
            raise RuntimeError('Cluster "{0}" unhealthy, aborting extend'.format(cluster_name))

        current_cluster = []
        for item in client.run('etcdctl member list').splitlines():
            info = re.search(EtcdInstaller.MEMBER_REGEX, item).groupdict()
            current_cluster.append('{0}={1}'.format(info['name'], info['peer']))

        client = SSHClient(new_ip, username='root')
        node_name = System.get_my_machine_id(client)
        current_cluster.append('{0}={1}'.format(node_name, EtcdInstaller.SERVER_URL.format(new_ip)))

        data_dir = EtcdInstaller.DATA_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        wal_dir = EtcdInstaller.WAL_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        abs_paths = [data_dir, wal_dir]
        client.dir_delete(abs_paths)
        client.dir_create(abs_paths)
        client.dir_chmod(abs_paths, 0755, recursive=True)
        client.dir_chown(abs_paths, 'ovs', 'ovs', recursive=True)

        base_name = 'ovs-etcd'
        target_name = 'ovs-etcd-{0}'.format(cluster_name)
        EtcdInstaller.stop(cluster_name, client)  # Stop a possible proxy service
        ServiceManager.add_service(base_name, client,
                                   params={'CLUSTER': cluster_name,
                                           'NODE_ID': node_name,
                                           'DATA_DIR': data_dir,
                                           'WAL_DIR': wal_dir,
                                           'SERVER_URL': EtcdInstaller.SERVER_URL.format(new_ip),
                                           'CLIENT_URL': EtcdInstaller.CLIENT_URL.format(new_ip),
                                           'LOCAL_CLIENT_URL': EtcdInstaller.CLIENT_URL.format('127.0.0.1'),
                                           'INITIAL_CLUSTER': ','.join(current_cluster),
                                           'INITIAL_STATE': 'existing',
                                           'INITIAL_PEERS': ''},
                                   target_name=target_name)

        master_client = SSHClient(master_ip, username='root')
        master_client.run('etcdctl member add {0} {1}'.format(node_name, EtcdInstaller.SERVER_URL.format(new_ip)))
        EtcdInstaller.start(cluster_name, client)
        EtcdInstaller.wait_for_cluster(cluster_name, client)

        logger.debug('Extending cluster "{0}" from {1} to {2} completed'.format(cluster_name, master_ip, new_ip))

    @staticmethod
    def shrink_cluster(remaining_node_ip, ip_to_remove, cluster_name, offline_node_ips=None):
        """
        Removes a node from a cluster, the old node will become a slave
        :param cluster_name: The name of the cluster to shrink
        :param ip_to_remove: The ip of the node that should be removed from the cluster
        :param remaining_node_ip: The ip of a remaining node in the cluster
        :param offline_node_ips: IPs of offline nodes
        """
        logger.debug('Shrinking cluster "{0}" from {1}'.format(cluster_name, ip_to_remove))

        current_client = SSHClient(remaining_node_ip, username='root')
        if not EtcdInstaller._is_healty(cluster_name, current_client):
            raise RuntimeError('Cluster "{0}" unhealthy, aborting shrink'.format(cluster_name))

        node_id = None
        for item in current_client.run('etcdctl member list').splitlines():
            info = re.search(EtcdInstaller.MEMBER_REGEX, item).groupdict()
            if EtcdInstaller.CLIENT_URL.format(ip_to_remove) == info['client']:
                node_id = info['id']
        if node_id is None:
            raise RuntimeError('Could not locate {0} in the cluster'.format(ip_to_remove))
        current_client.run('etcdctl member remove {0}'.format(node_id))
        if ip_to_remove not in offline_node_ips:
            EtcdInstaller.deploy_to_slave(remaining_node_ip, ip_to_remove, cluster_name)
        EtcdInstaller.wait_for_cluster(cluster_name, current_client)

        logger.debug('Shrinking cluster "{0}" from {1} completed'.format(cluster_name, ip_to_remove))

    @staticmethod
    def deploy_to_slave(master_ip, slave_ip, cluster_name):
        """
        Deploys the configuration file to a slave
        :param cluster_name: Name of the cluster of which to deploy the configuration file
        :param slave_ip: IP of the slave to deploy to
        :param master_ip: IP of the node to deploy from
        """
        logger.debug('  Setting up proxy "{0}" from {1} to {2}'.format(cluster_name, master_ip, slave_ip))
        master_client = SSHClient(master_ip, username='root')
        slave_client = SSHClient(slave_ip, username='root')

        current_cluster = []
        for item in master_client.run('etcdctl member list').splitlines():
            info = re.search(EtcdInstaller.MEMBER_REGEX, item).groupdict()
            current_cluster.append('{0}={1}'.format(info['name'], info['peer']))

        EtcdInstaller._setup_proxy(','.join(current_cluster), slave_client, cluster_name)
        logger.debug('  Setting up proxy "{0}" from {1} to {2} completed'.format(cluster_name, master_ip, slave_ip))

    @staticmethod
    def use_external(external, slave_ip, cluster_name):
        """
        Setup proxy for external etcd
        :param external: External etcd info
        :param slave_ip: IP of slave
        :param cluster_name: Name of cluster
        """
        logger.debug('Setting up proxy "{0}" from {1} to {2}'.format(cluster_name, external, slave_ip))
        EtcdInstaller._setup_proxy(external, SSHClient(slave_ip, username='root'), cluster_name)
        logger.debug('Setting up proxy "{0}" from {1} to {2} completed'.format(cluster_name, external, slave_ip))

    @staticmethod
    def _setup_proxy(initial_cluster, slave_client, cluster_name):
        base_name = 'ovs-etcd-proxy'
        target_name = 'ovs-etcd-{0}'.format(cluster_name)
        EtcdInstaller.stop(cluster_name, slave_client)

        data_dir = EtcdInstaller.DATA_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        wal_dir = EtcdInstaller.WAL_DIR.format(EtcdInstaller.DB_DIR, cluster_name)
        abs_paths = [data_dir, wal_dir]
        slave_client.dir_delete(abs_paths)
        slave_client.dir_create(data_dir)
        slave_client.dir_chmod(data_dir, 0755, recursive=True)
        slave_client.dir_chown(data_dir, 'ovs', 'ovs', recursive=True)

        ServiceManager.add_service(base_name, slave_client,
                                   params={'CLUSTER': cluster_name,
                                           'DATA_DIR': data_dir,
                                           'LOCAL_CLIENT_URL': EtcdInstaller.CLIENT_URL.format('127.0.0.1'),
                                           'INITIAL_CLUSTER': initial_cluster},
                                   target_name=target_name)
        EtcdInstaller.start(cluster_name, slave_client)
        EtcdInstaller.wait_for_cluster(cluster_name, slave_client)

    @staticmethod
    def start(cluster_name, client):
        """
        Starts an etcd cluster
        :param client: Client on which to start the service
        :param cluster_name: The name of the cluster service to start
        """
        if ServiceManager.has_service('etcd-{0}'.format(cluster_name), client=client) is True and \
                ServiceManager.get_service_status('etcd-{0}'.format(cluster_name), client=client) is False:
            ServiceManager.start_service('etcd-{0}'.format(cluster_name), client=client)

    @staticmethod
    def stop(cluster_name, client):
        """
        Stops an etcd service
        :param client: Client on which to stop the service
        :param cluster_name: The name of the cluster service to stop
        """
        if ServiceManager.has_service('etcd-{0}'.format(cluster_name), client=client) is True and \
                ServiceManager.get_service_status('etcd-{0}'.format(cluster_name), client=client) is True:
            ServiceManager.stop_service('etcd-{0}'.format(cluster_name), client=client)

    @staticmethod
    def remove(cluster_name, client):
        """
        Removes an etcd service
        :param client: Client on which to remove the service
        :param cluster_name: The name of the cluster service to remove
        """
        if ServiceManager.has_service('etcd-{0}'.format(cluster_name), client=client) is True:
            ServiceManager.remove_service('etcd-{0}'.format(cluster_name), client=client)

    @staticmethod
    def wait_for_cluster(cluster_name, client):
        """
        Validates the health of the etcd cluster is healthy
        :param client: The client on which to validate the cluster
        :param cluster_name: Name of the cluster
        """
        logger.debug('Waiting for cluster "{0}"'.format(cluster_name))
        tries = 5
        healthy = EtcdInstaller._is_healty(cluster_name, client)
        while healthy is False and tries > 0:
            tries -= 1
            time.sleep(5 - tries)
            healthy = EtcdInstaller._is_healty(cluster_name, client)
        if healthy is False:
            raise RuntimeError('Etcd cluster "{0}" could not be started correctly'.format(cluster_name))
        logger.debug('Cluster "{0}" running'.format(cluster_name))

    @staticmethod
    def _is_healty(cluster_name, client):
        """
        Indicates whether a given cluster is healthy
        :param cluster_name: name of the cluster
        :param client: client on which to check
        """
        try:
            output = client.run('etcdctl cluster-health')
            if 'cluster is healthy' not in output:
                logger.debug('  Cluster "{0}" is not healthy: {1}'.format(cluster_name, ' - '.join(output.splitlines())))
                return False
            logger.debug('  Cluster "{0}" is healthy'.format(cluster_name))
            return True
        except Exception as ex:
            logger.debug('  Cluster "{0}" is not healthy: {1}'.format(cluster_name, ex))
            return False
