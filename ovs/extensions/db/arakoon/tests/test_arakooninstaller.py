# Copyright (C) 2016 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of any kind.

"""
Module for testing the ArakoonInstaller
"""
import os
import json
import shutil
import unittest
from threading import Thread
from ovs.dal.hybrids.servicetype import ServiceType
from ovs.dal.tests.helpers import DalHelper
from ovs.extensions.db.arakoon.ArakoonInstaller import ArakoonClusterConfig, ArakoonInstaller
from ovs.extensions.generic.configuration import Configuration, NotFoundException
from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.services.service import ServiceManager
from ovs.extensions.tests.helpers import ExtensionsHelper


class ArakoonInstallerTester(unittest.TestCase):
    """
    This test class will validate the various scenarios of the MDSService logic
    """
    cluster_name = None
    arakoon_client = None
    EXPECTED_CLUSTER_CONFIG = """[global]
cluster = {node_names}
cluster_id = {cluster_id}
plugins = {plugins}
tlog_max_entries = 5000

"""  # Enters required (Do not remove)

    EXPECTED_NODE_CONFIG = """[{node_name}]
client_port = {client_port}
crash_log_sinks = console:
fsync = true
home = {base_dir}/arakoon/{cluster_name}/db
ip = {ip}
log_level = info
log_sinks = console:
messaging_port = {messaging_port}
name = {node_name}
tlog_compression = snappy
tlog_dir = {base_dir}/arakoon/{cluster_name}/tlogs

"""  # Enters required (Do not remove)

    def setUp(self):
        """
        (Re)Sets the stores on every test
        """
        DalHelper.setup()
        self.maxDiff = None

    def tearDown(self):
        """
        Clean up test suite
        """
        DalHelper.teardown()

    def _validate_dir_structure(self, expected_structure, directories):
        for directory in directories:
            self.assertDictEqual(d1=ExtensionsHelper.extract_dir_structure(directory),
                                 d2=expected_structure)

    def _validate_config(self, node_info, plugins=None, filesystem=False):
        expected_config = ArakoonInstallerTester.EXPECTED_CLUSTER_CONFIG.format(node_names=','.join(node['name'] for node in node_info),
                                                                                cluster_id=cluster_name,
                                                                                plugins=plugins if plugins is not None else '')
        for info in node_info:
            expected_config += ArakoonInstallerTester.EXPECTED_NODE_CONFIG.format(ip=info['ip'],
                                                                                  base_dir=info['base_dir'],
                                                                                  node_name=info['name'],
                                                                                  client_port=info['ports'][0],
                                                                                  cluster_name=cluster_name,
                                                                                  messaging_port=info['ports'][1])
        if filesystem is True:
            ip = node_info[0]['ip']
            client = SSHClient(endpoint=ip)
            config = ArakoonClusterConfig(cluster_id=cluster_name, source_ip=ip)
            actual = client.file_read(filename=config.internal_config_path)
        else:
            actual = Configuration.get(ArakoonClusterConfig.CONFIG_KEY.format(cluster_name), raw=True)
        self.assertEqual(first=actual,
                         second=expected_config)
        self.assertEqual(first=arakoon_client.get(ArakoonInstaller.INTERNAL_CONFIG_KEY),
                         second=expected_config)
        self.assertTrue(expr=arakoon_client.exists(ArakoonInstaller.METADATA_KEY))

    def _validate_services(self, name, service_info):
        for info in service_info:
            status = info['status']
            storagerouter = info['storagerouter']
            service_metadata = info.get('service_metadata', None)

            # Assert Arakoon service status
            ssh_client = SSHClient(endpoint=storagerouter.ip, username='root')
            if status == 'running':
                self.assertTrue(expr=ServiceManager.has_service(name=name, client=ssh_client))
                self.assertTrue(expr=ServiceManager.get_service_status(name=name, client=ssh_client)[0])
            elif status == 'halted':
                self.assertTrue(expr=ServiceManager.has_service(name=name, client=ssh_client))
                self.assertFalse(expr=ServiceManager.get_service_status(name=name, client=ssh_client)[0])
            else:
                self.assertFalse(expr=ServiceManager.has_service(name=name, client=ssh_client))

            # Assert service metadata is stored correctly in configuration management
            self.assertEqual(first=service_metadata is not None,
                             second=Configuration.exists(key='/ovs/framework/hosts/{0}/services/{1}'.format(storagerouter.name, name)))
            if service_metadata is not None:
                self.assertDictEqual(d1=service_metadata,
                                     d2=Configuration.get(key='/ovs/framework/hosts/{0}/services/{1}'.format(storagerouter.name, name)))

    def _validate_metadata(self, cluster_type, in_use, internal=True):
        self.assertDictEqual(d1=json.loads(arakoon_client.get(ArakoonInstaller.METADATA_KEY)),
                             d2={'cluster_name': cluster_name,
                                 'cluster_type': cluster_type,
                                 'in_use': in_use,
                                 'internal': internal})

    def test_internal_not_on_filesystem(self):
        """
        Validates whether a cluster of type FWK can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 3 nodes.
          - Plugins: None
          - Internal: True
          - File System: False
          - Cluster type: FWK
        """
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'internal_fwk'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        storagerouter_3 = structure['storagerouters'][3]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        mountpoint_3 = storagerouter_3.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_internal_fwk'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_internal_fwk_extend'.format(mountpoint_2)
        base_dir_3 = '{0}/base_dir_internal_fwk_extend_port_range'.format(mountpoint_3)

        for mountpoint in [mountpoint_1, mountpoint_2, mountpoint_3]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        # Basic validations
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type='UNKNOWN',
                                            ip=storagerouter_1.ip,
                                            base_dir=base_dir_1)
        self.assertIn(member=', '.join(sorted(ServiceType.ARAKOON_CLUSTER_TYPES)),
                      container=raise_info.exception.message)

        with self.assertRaises(ValueError) as raise_info:
            # noinspection PyTypeChecker
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                            ip=storagerouter_1.ip,
                                            base_dir=base_dir_1,
                                            plugins=[])
        self.assertEqual(first='Plugins should be a dict',
                         second=raise_info.exception.message)

        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                            ip=storagerouter_1.ip,
                                            base_dir=base_dir_1,
                                            port_range=[[20000, 20000]])
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required
                         second=raise_info.exception.message)

        ##########
        # CREATE #
        ##########
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1)
        # Attempt to recreate a cluster with the same name
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                            ip=storagerouter_2.ip,
                                            base_dir=base_dir_2)
        self.assertIn(member='"{0}" already exists'.format(cluster_name),
                      container=raise_info.exception.message)

        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'])
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=True)

        # Un-claim and claim
        ArakoonInstaller.unclaim_cluster(cluster_name=cluster_name)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=False)
        ArakoonInstaller.claim_cluster(cluster_name=cluster_name)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=True)

        ##########
        # EXTEND #
        ##########
        with self.assertRaises(ValueError) as raise_info:
            # noinspection PyTypeChecker
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_2.ip,
                                            base_dir=base_dir_2,
                                            plugins=[])
        self.assertEqual(first='Plugins should be a dict',
                         second=raise_info.exception.message)

        extend_info_1 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_2.ip,
                                                        base_dir=base_dir_2)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip],
                                             new_ip=storagerouter_2.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=True)

        # Try to extend with specific port_range
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_3.ip,
                                            base_dir=base_dir_3,
                                            port_range=[[30000, 30000]])
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required and with this range, only 1 is available
                         second=raise_info.exception.message)

        # Extend with specific port range
        extend_info_2 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_3.ip,
                                                        base_dir=base_dir_3,
                                                        port_range=[30000])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'halted', 'service_metadata': extend_info_2['service_metadata']}])

        # Restart cluster
        catchup_command = 'arakoon --node 3 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip, storagerouter_2.ip],
                                             new_ip=storagerouter_3.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=True)

        ##########
        # SHRINK #
        ##########
        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_config(node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK,
                                in_use=True)

        ##########
        # DELETE #
        ##########
        ArakoonInstaller.delete_cluster(cluster_name=cluster_name)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self.assertFalse(expr=Configuration.exists(ArakoonClusterConfig.CONFIG_KEY.format(cluster_name), raw=True))
        with self.assertRaises(NotFoundException):
            ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))

    def test_external_not_on_filesystem(self):
        """
        Validates whether an external cluster of type SD can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 3 nodes.
          - Plugins: None
          - Internal: False
          - File System: False
          - Cluster type: SD
        """
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'external_sd'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        storagerouter_3 = structure['storagerouters'][3]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        mountpoint_3 = storagerouter_3.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_external_sd'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_external_sd_extend'.format(mountpoint_2)
        base_dir_3 = '{0}/base_dir_external_sd_extend_port_range'.format(mountpoint_3)

        for mountpoint in [mountpoint_1, mountpoint_2, mountpoint_3]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        ##########
        # CREATE #
        ##########
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1,
                                                      internal=False)
        # Attempt to recreate a cluster with the same name
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                            ip=storagerouter_2.ip,
                                            base_dir=base_dir_2)
        self.assertIn(member='"{0}" already exists'.format(cluster_name),
                      container=raise_info.exception.message)

        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'])
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=True,
                                internal=False)

        # Un-claim and claim
        ArakoonInstaller.unclaim_cluster(cluster_name=cluster_name)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=False,
                                internal=False)
        ArakoonInstaller.claim_cluster(cluster_name=cluster_name)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=True,
                                internal=False)

        ##########
        # EXTEND #
        ##########
        extend_info_1 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_2.ip,
                                                        base_dir=base_dir_2)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip, storagerouter_2.ip],
                                             new_ip=storagerouter_2.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=True,
                                internal=False)

        # Try to extend with specific port_range
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_3.ip,
                                            base_dir=base_dir_3,
                                            port_range=[[30000, 30000]])
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required and with this range, only 1 is available
                         second=raise_info.exception.message)

        # Extend with specific port range
        extend_info_2 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_3.ip,
                                                        base_dir=base_dir_3,
                                                        port_range=[30000])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'halted', 'service_metadata': extend_info_2['service_metadata']}])

        # Restart cluster
        catchup_command = 'arakoon --node 3 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip, storagerouter_2.ip],
                                             new_ip=storagerouter_3.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=True,
                                internal=False)

        ##########
        # SHRINK #
        ##########
        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_config(node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.SD,
                                in_use=True,
                                internal=False)

        ##########
        # DELETE #
        ##########
        ArakoonInstaller.delete_cluster(cluster_name=cluster_name)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self.assertFalse(expr=Configuration.exists(ArakoonClusterConfig.CONFIG_KEY.format(cluster_name), raw=True))
        with self.assertRaises(NotFoundException):
            ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))

    def test_internal_not_on_filesystem_with_plugins(self):
        """
        Validates whether a cluster of type ABM with usage of plugins can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 2 nodes.
          - Plugins: 2 plugins
          - Internal: True
          - File System: False
          - Cluster type: ABM
        """
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'internal_abm_with_plugins'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_internal_abm_plugins'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_internal_abm_plugins_extend'.format(mountpoint_2)

        for mountpoint in [mountpoint_1, mountpoint_2]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        ##########
        # CREATE #
        ##########
        plugins = {'plugin1': 'command1',
                   'plugin2': 'command2'}
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1,
                                                      plugins=plugins)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'])
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                in_use=True)
        self.assertIn(member='EXTRA_VERSION_CMD',
                      container=create_info['service_metadata'])
        self.assertEqual(first=';'.join(plugins.values()),
                         second=create_info['service_metadata']['EXTRA_VERSION_CMD'])

        ##########
        # EXTEND #
        ##########
        extend_info = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                      new_ip=storagerouter_2.ip,
                                                      base_dir=base_dir_2,
                                                      plugins=plugins)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info['service_metadata']}])

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip],
                                             new_ip=storagerouter_2.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                in_use=True)
        self.assertIn(member='EXTRA_VERSION_CMD',
                      container=extend_info['service_metadata'])
        self.assertEqual(first=';'.join(plugins.values()),
                         second=extend_info['service_metadata']['EXTRA_VERSION_CMD'])

        ##########
        # SHRINK #
        ##########
        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info['service_metadata']}])
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info['ports']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                in_use=True)

        ##########
        # DELETE #
        ##########
        ArakoonInstaller.delete_cluster(cluster_name=cluster_name)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])
        self.assertFalse(expr=Configuration.exists(ArakoonClusterConfig.CONFIG_KEY.format(cluster_name), raw=True))
        with self.assertRaises(NotFoundException):
            ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))

    def test_external_not_on_filesystem_with_plugins(self):
        """
        Validates whether an external cluster of type NSM with usage of plugins can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 2 nodes.
          - Plugins: 2 plugins
          - Internal: False
          - File System: False
          - Cluster type: NSM
        """
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'internal_nsm_with_plugins'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_internal_nsm_plugins'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_internal_nsm_plugins_extend'.format(mountpoint_2)

        for mountpoint in [mountpoint_1, mountpoint_2]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        ##########
        # CREATE #
        ##########
        plugins = {'plugin1': 'command1',
                   'plugin2': 'command2'}
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.NSM,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1,
                                                      plugins=plugins,
                                                      internal=False)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'])
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.NSM,
                                in_use=True,
                                internal=False)
        self.assertIn(member='EXTRA_VERSION_CMD',
                      container=create_info['service_metadata'])
        self.assertEqual(first=';'.join(plugins.values()),
                         second=create_info['service_metadata']['EXTRA_VERSION_CMD'])

        ##########
        # EXTEND #
        ##########
        extend_info = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                      new_ip=storagerouter_2.ip,
                                                      base_dir=base_dir_2,
                                                      plugins=plugins)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info['service_metadata']}])

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config file://opt/OpenvStorage/config/framework.json?key=/ovs/arakoon/{0}/config -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip],
                                             new_ip=storagerouter_2.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.NSM,
                                in_use=True,
                                internal=False)
        self.assertIn(member='EXTRA_VERSION_CMD',
                      container=extend_info['service_metadata'])
        self.assertEqual(first=';'.join(plugins.values()),
                         second=extend_info['service_metadata']['EXTRA_VERSION_CMD'])

        ##########
        # SHRINK #
        ##########
        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info['service_metadata']}])
        self._validate_config(plugins=','.join(plugins.keys()),
                              node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info['ports']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.NSM,
                                in_use=True,
                                internal=False)

        ##########
        # DELETE #
        ##########
        ArakoonInstaller.delete_cluster(cluster_name=cluster_name)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'}])
        self.assertFalse(expr=Configuration.exists(ArakoonClusterConfig.CONFIG_KEY.format(cluster_name), raw=True))
        with self.assertRaises(NotFoundException):
            ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name))

    def test_internal_on_filesystem(self):
        """
        Validates whether a cluster of type CFG can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 3 nodes.
          - Plugins: None
          - Internal: True
          - File System: True
          - Cluster type: CFG
        """
        import time
        start = time.time()
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'unittest_internal_cfg'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        storagerouter_3 = structure['storagerouters'][3]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        mountpoint_3 = storagerouter_3.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_internal_cfg'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_internal_cfg_extend'.format(mountpoint_2)
        base_dir_3 = '{0}/base_dir_internal_cfg_extend_port_range'.format(mountpoint_3)

        for mountpoint in [mountpoint_1, mountpoint_2, mountpoint_3]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        print '1: {0}'.format(time.time() - start)

        # Basic validations
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                            ip=storagerouter_1.ip,
                                            base_dir=base_dir_1,
                                            port_range=[[20000, 20000]])
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required
                         second=raise_info.exception.message)
        print '2: {0}'.format(time.time() - start)
        ##########
        # CREATE #
        ##########
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1)
        print '3: {0}'.format(time.time() - start)
        # Attempt to recreate a cluster with the same name
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                            ip=storagerouter_2.ip,
                                            base_dir=base_dir_2)
        self.assertIn(member='"{0}" already exists'.format(cluster_name),
                      container=raise_info.exception.message)
        print '4: {0}'.format(time.time() - start)

        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        print '5: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '6: {0}'.format(time.time() - start)

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'], ip=storagerouter_1.ip)
        print '7: {0}'.format(time.time() - start)
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name, source_ip=storagerouter_1.ip))
        print '8: {0}'.format(time.time() - start)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        print '9: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '10: {0}'.format(time.time() - start)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True)
        print '11: {0}'.format(time.time() - start)
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_1.machine_id,
                                        service_metadata=create_info['service_metadata'])
        print '12: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '13: {0}'.format(time.time() - start)

        # Un-claim and claim
        ArakoonInstaller.unclaim_cluster(cluster_name=cluster_name, ip=storagerouter_1.ip)
        print '14: {0}'.format(time.time() - start)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=False)
        print '15: {0}'.format(time.time() - start)
        ArakoonInstaller.claim_cluster(cluster_name=cluster_name, ip=storagerouter_1.ip)
        print '16: {0}'.format(time.time() - start)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True)
        print '17: {0}'.format(time.time() - start)

        ##########
        # EXTEND #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying an IP to extend a cluster of type CFG should raise
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_2.ip,
                                            base_dir=base_dir_2)
        print '18: {0}'.format(time.time() - start)

        extend_info_1 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_2.ip,
                                                        base_dir=base_dir_2,
                                                        ip=storagerouter_1.ip)
        print '19: {0}'.format(time.time() - start)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        print '20: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '21: {0}'.format(time.time() - start)
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_2.machine_id,
                                        service_metadata=extend_info_1['service_metadata'])
        print '22: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '23: {0}'.format(time.time() - start)

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config /opt/OpenvStorage/config/arakoon_{0}.ini -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip],
                                             new_ip=storagerouter_2.ip)
        print '24: {0}'.format(time.time() - start)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']}])
        print '25: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '26: {0}'.format(time.time() - start)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True)
        print '27: {0}'.format(time.time() - start)

        # Try to extend with specific port_range
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_3.ip,
                                            base_dir=base_dir_3,
                                            ip=storagerouter_1.ip,
                                            port_range=[[30000, 30000]])
        print '28: {0}'.format(time.time() - start)
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required and with this range, only 1 is available
                         second=raise_info.exception.message)

        # Extend with specific port range
        extend_info_2 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_3.ip,
                                                        base_dir=base_dir_3,
                                                        ip=storagerouter_1.ip,
                                                        port_range=[30000])
        print '29: {0}'.format(time.time() - start)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        print '30: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'halted'}])
        print '31: {0}'.format(time.time() - start)

        # Restart cluster
        catchup_command = 'arakoon --node 3 -config /opt/OpenvStorage/config/arakoon_{0}.ini -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip, storagerouter_2.ip, storagerouter_3.ip],  # Restart should be able to coop with 'new_ip' inside 'current_ips'
                                             new_ip=storagerouter_3.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        print '32: {0}'.format(time.time() - start)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        print '33: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running'}])
        print '34: {0}'.format(time.time() - start)
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_3.machine_id,
                                        service_metadata=extend_info_2['service_metadata'])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True)
        print '35: {0}'.format(time.time() - start)

        ##########
        # SHRINK #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying a remaining IP to shrink a cluster of type CFG should raise
            ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                            ip=storagerouter_1.ip)
        print '36: {0}'.format(time.time() - start)

        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip,
                                        remaining_ip=storagerouter_2.ip)
        print '37: {0}'.format(time.time() - start)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2, base_dir_3])
        print '38: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        print '39: {0}'.format(time.time() - start)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        print '40: {0}'.format(time.time() - start)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True)
        print '41: {0}'.format(time.time() - start)

        ##########
        # DELETE #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying an IP to delete a cluster of type CFG should raise
            ArakoonInstaller.delete_cluster(cluster_name=cluster_name)
        print '42: {0}'.format(time.time() - start)

        ArakoonInstaller.delete_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_2.ip)
        print '43: {0}'.format(time.time() - start)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        print '44: {0}'.format(time.time() - start)
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        print '45: {0}'.format(time.time() - start)
        client = SSHClient(endpoint=storagerouter_2.ip)
        self.assertFalse(expr=client.file_exists('/opt/OpenvStorage/config/arakoon_{0}.ini'.format(cluster_name)))
        print '46: {0}'.format(time.time() - start)

    def test_external_on_filesystem(self):
        """
        Validates whether an external cluster of type CFG can be correctly created, extended, shrunken and deleted
        This tests will deploy and extend an Arakoon cluster on 3 nodes.
          - Plugins: None
          - Internal: False
          - File System: True
          - Cluster type: CFG
        """
        global cluster_name, arakoon_client
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1, 2, 3]})
        cluster_name = 'unittest_external_cfg'
        service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
        storagerouter_1 = structure['storagerouters'][1]
        storagerouter_2 = structure['storagerouters'][2]
        storagerouter_3 = structure['storagerouters'][3]
        mountpoint_1 = storagerouter_1.disks[0].partitions[0].mountpoint
        mountpoint_2 = storagerouter_2.disks[0].partitions[0].mountpoint
        mountpoint_3 = storagerouter_3.disks[0].partitions[0].mountpoint
        base_dir_1 = '{0}/base_dir_external_cfg'.format(mountpoint_1)
        base_dir_2 = '{0}/base_dir_external_cfg_extend'.format(mountpoint_2)
        base_dir_3 = '{0}/base_dir_external_cfg_extend_port_range'.format(mountpoint_3)

        for mountpoint in [mountpoint_1, mountpoint_2, mountpoint_3]:
            if os.path.exists(mountpoint) and mountpoint != '/':
                shutil.rmtree(mountpoint)

        expected_structure = {'files': [],
                              'dirs': {'arakoon': {'files': [],
                                                   'dirs': {cluster_name: {'files': [],
                                                                           'dirs': {'db': {'dirs': {}, 'files': []},
                                                                                    'tlogs': {'dirs': {}, 'files': []}}}}}}}
        expected_structure_after_removal = {'files': [],
                                            'dirs': {'arakoon': {'files': [],
                                                                 'dirs': {cluster_name: {'dirs': {}, 'files': []}}}}}

        # Basic validations
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                            ip=storagerouter_1.ip,
                                            base_dir=base_dir_1,
                                            port_range=[[20000, 20000]],
                                            internal=False)
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required
                         second=raise_info.exception.message)

        ##########
        # CREATE #
        ##########
        create_info = ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                                      cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                                      ip=storagerouter_1.ip,
                                                      base_dir=base_dir_1,
                                                      internal=False)
        # Attempt to recreate a cluster with the same name
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.create_cluster(cluster_name=cluster_name,
                                            cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                            ip=storagerouter_2.ip,
                                            base_dir=base_dir_2,
                                            internal=False)
        self.assertIn(member='"{0}" already exists'.format(cluster_name),
                      container=raise_info.exception.message)

        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'halted'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Start cluster
        ArakoonInstaller.start_cluster(metadata=create_info['metadata'], ip=storagerouter_1.ip)
        arakoon_client = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cluster_name, source_ip=storagerouter_1.ip))
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True,
                                internal=False)
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_1.machine_id,
                                        service_metadata=create_info['service_metadata'])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Un-claim and claim
        ArakoonInstaller.unclaim_cluster(cluster_name=cluster_name, ip=storagerouter_1.ip)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=False,
                                internal=False)
        ArakoonInstaller.claim_cluster(cluster_name=cluster_name, ip=storagerouter_1.ip)
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True,
                                internal=False)

        ##########
        # EXTEND #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying an IP to extend a cluster of type CFG should raise
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_2.ip,
                                            base_dir=base_dir_2)

        extend_info_1 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_2.ip,
                                                        base_dir=base_dir_2,
                                                        ip=storagerouter_1.ip)
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_2.machine_id,
                                        service_metadata=extend_info_1['service_metadata'])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'halted', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])

        # Restart cluster
        catchup_command = 'arakoon --node 2 -config /opt/OpenvStorage/config/arakoon_{0}.ini -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip],
                                             new_ip=storagerouter_2.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True,
                                internal=False)

        # Try to extend with specific port_range
        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                            new_ip=storagerouter_3.ip,
                                            base_dir=base_dir_3,
                                            ip=storagerouter_1.ip,
                                            port_range=[[30000, 30000]])
        self.assertEqual(first='Unable to find requested nr of free ports',  # 2 free ports are required and with this range, only 1 is available
                         second=raise_info.exception.message)

        # Extend with specific port range
        extend_info_2 = ArakoonInstaller.extend_cluster(cluster_name=cluster_name,
                                                        new_ip=storagerouter_3.ip,
                                                        base_dir=base_dir_3,
                                                        ip=storagerouter_1.ip,
                                                        port_range=[30000])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'halted'}])

        # Restart cluster
        catchup_command = 'arakoon --node 3 -config /opt/OpenvStorage/config/arakoon_{0}.ini -catchup-only'.format(cluster_name)
        SSHClient._run_returns[catchup_command] = None
        SSHClient._run_recordings = []
        ArakoonInstaller.restart_cluster_add(cluster_name=cluster_name,
                                             current_ips=[storagerouter_1.ip, storagerouter_2.ip, storagerouter_3.ip],  # Restart should be able to coop with 'new_ip' inside 'current_ips'
                                             new_ip=storagerouter_3.ip)
        self.assertIn(catchup_command, SSHClient._run_recordings)
        self._validate_config(filesystem=True,
                              node_info=[{'name': '1', 'ip': storagerouter_1.ip, 'base_dir': base_dir_1, 'ports': create_info['ports']},
                                         {'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running'}])
        # Register the service
        ServiceManager.register_service(node_name=storagerouter_3.machine_id,
                                        service_metadata=extend_info_2['service_metadata'])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'running', 'service_metadata': create_info['service_metadata']},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True,
                                internal=False)

        ##########
        # SHRINK #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying a remaining IP to shrink a cluster of type CFG should raise
            ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                            ip=storagerouter_1.ip)

        ArakoonInstaller.shrink_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_1.ip,
                                        remaining_ip=storagerouter_2.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1])
        self._validate_dir_structure(expected_structure=expected_structure,
                                     directories=[base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'running', 'service_metadata': extend_info_1['service_metadata']},
                                              {'storagerouter': storagerouter_3, 'status': 'running', 'service_metadata': extend_info_2['service_metadata']}])
        self._validate_config(filesystem=True,
                              node_info=[{'name': '2', 'ip': storagerouter_2.ip, 'base_dir': base_dir_2, 'ports': extend_info_1['ports']},
                                         {'name': '3', 'ip': storagerouter_3.ip, 'base_dir': base_dir_3, 'ports': extend_info_2['ports']}])
        self._validate_metadata(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG,
                                in_use=True,
                                internal=False)

        ##########
        # DELETE #
        ##########
        with self.assertRaises(NotFoundException):  # Not specifying an IP to delete a cluster of type CFG should raise
            ArakoonInstaller.delete_cluster(cluster_name=cluster_name)

        ArakoonInstaller.delete_cluster(cluster_name=cluster_name,
                                        ip=storagerouter_2.ip)
        self._validate_dir_structure(expected_structure=expected_structure_after_removal,
                                     directories=[base_dir_1, base_dir_2, base_dir_3])
        self._validate_services(name=service_name,
                                service_info=[{'storagerouter': storagerouter_1, 'status': 'missing'},
                                              {'storagerouter': storagerouter_2, 'status': 'missing'},
                                              {'storagerouter': storagerouter_3, 'status': 'missing'}])
        client = SSHClient(endpoint=storagerouter_2.ip)
        self.assertFalse(expr=client.file_exists('/opt/OpenvStorage/config/arakoon_{0}.ini'.format(cluster_name)))

    def test_get_unused_arakoon_metadata_and_claim(self):
        """
        Test the method 'get_unused_arakoon_metadata_and_claim'
         - Amount threads >> amount clusters
         - Amount threads >> amount clusters with specific cluster_name
        """
        amount_threads = 10
        amount_clusters = 3
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1]})
        storagerouter = structure['storagerouters'][1]
        mountpoint = storagerouter.disks[0].partitions[0].mountpoint
        if os.path.exists(mountpoint) and mountpoint != '/':
            shutil.rmtree(mountpoint)

        # Create less clusters than threads. Due to locking, this shouldn't invoke any error and all clusters should be claimed
        cluster_map = {}
        for index in range(amount_clusters):
            cl_name = 'unittest_cluster_test1_{0}'.format(index)
            create_info = ArakoonInstaller.create_cluster(cluster_name=cl_name,
                                                          cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                                          ip=storagerouter.ip,
                                                          base_dir='{0}/base_dir_claim'.format(mountpoint),
                                                          internal=False)
            ArakoonInstaller.start_cluster(metadata=create_info['metadata'], ip=storagerouter.ip)
            ArakoonInstaller.unclaim_cluster(cluster_name=cl_name)
            cluster_map[cl_name] = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cl_name))

        for cl_name, client in cluster_map.iteritems():
            self.assertDictEqual(d1=json.loads(client.get(ArakoonInstaller.METADATA_KEY)),
                                 d2={'cluster_name': cl_name,
                                     'cluster_type': ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                     'in_use': False,
                                     'internal': False})

        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.get_unused_arakoon_metadata_and_claim(cluster_type='UNKNOWN')
        self.assertIn(member=', '.join(sorted(ServiceType.ARAKOON_CLUSTER_TYPES)),
                      container=raise_info.exception.message)

        threads = []
        for x in range(amount_threads):
            thread = Thread(target=ArakoonInstaller.get_unused_arakoon_metadata_and_claim,
                            name='unittest_thread{0}'.format(x),
                            args=(ServiceType.ARAKOON_CLUSTER_TYPES.ABM,))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        for cl_name, client in cluster_map.iteritems():
            self.assertDictEqual(d1=json.loads(client.get(ArakoonInstaller.METADATA_KEY)),
                                 d2={'cluster_name': cl_name,
                                     'cluster_type': ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                     'in_use': True,
                                     'internal': False})

        # Create less clusters than threads, but try to claim a specific cluster. Due to locking, this shouldn't invoke any error and the specified should be claimed
        cluster_map = {}
        for index in range(amount_clusters):
            cl_name = 'unittest_cluster_test2_{0}'.format(index)
            create_info = ArakoonInstaller.create_cluster(cluster_name=cl_name,
                                                          cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                                          ip=storagerouter.ip,
                                                          base_dir='{0}/base_dir_claim'.format(mountpoint),
                                                          internal=False)
            ArakoonInstaller.start_cluster(metadata=create_info['metadata'], ip=storagerouter.ip)
            ArakoonInstaller.unclaim_cluster(cluster_name=cl_name)  # Starting the cluster will mark the cluster as 'in_use'
            cluster_map[cl_name] = ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cl_name))

        for cl_name, client in cluster_map.iteritems():
            self.assertDictEqual(d1=json.loads(client.get(ArakoonInstaller.METADATA_KEY)),
                                 d2={'cluster_name': cl_name,
                                     'cluster_type': ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                     'in_use': False,
                                     'internal': False})

        threads = []
        cluster_to_claim = cluster_map.keys()[0]
        for x in range(amount_threads):
            thread = Thread(target=ArakoonInstaller.get_unused_arakoon_metadata_and_claim,
                            name='unittest_thread{0}'.format(x),
                            args=(ServiceType.ARAKOON_CLUSTER_TYPES.ABM, cluster_to_claim))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        for cl_name, client in cluster_map.iteritems():
            in_use = False
            if cl_name == cluster_to_claim:
                in_use = True
            self.assertDictEqual(d1=json.loads(client.get(ArakoonInstaller.METADATA_KEY)),
                                 d2={'cluster_name': cl_name,
                                     'cluster_type': ServiceType.ARAKOON_CLUSTER_TYPES.ABM,
                                     'in_use': in_use,
                                     'internal': False})

    def test_get_unused_arakoon_clusters(self):
        """
        Test the method 'get_unused_arakoon_clusters'
        """
        structure = DalHelper.build_dal_structure(structure={'storagerouters': [1]})
        storagerouter = structure['storagerouters'][1]
        mountpoint = storagerouter.disks[0].partitions[0].mountpoint
        if os.path.exists(mountpoint) and mountpoint != '/':
            shutil.rmtree(mountpoint)

        # Create some clusters for 2 types
        cluster_map = {}
        for cluster_type, amount in {ServiceType.ARAKOON_CLUSTER_TYPES.FWK: 3,
                                     ServiceType.ARAKOON_CLUSTER_TYPES.ABM: 2}.iteritems():
            for index in range(amount):
                cl_name = 'unittest_cluster_{0}_{1}'.format(cluster_type, index)
                create_info = ArakoonInstaller.create_cluster(cluster_name=cl_name,
                                                              cluster_type=cluster_type,
                                                              ip=storagerouter.ip,
                                                              base_dir='{0}/base_dir_unused'.format(mountpoint),
                                                              internal=False)
                ArakoonInstaller.start_cluster(metadata=create_info['metadata'])
                if index != 0:  # 2nd, 3rd FWK cluster and 2nd ABM cluster will be 'in_use' False
                    ArakoonInstaller.unclaim_cluster(cluster_name=cl_name)
                cluster_map[cl_name] = {'cluster_type': cluster_type,
                                        'arakoon_client': ArakoonInstaller.build_client(ArakoonClusterConfig(cluster_id=cl_name))}

        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.get_unused_arakoon_clusters(cluster_type='UNKNOWN')
        expected_types = ServiceType.ARAKOON_CLUSTER_TYPES.keys()
        expected_types.remove(ServiceType.ARAKOON_CLUSTER_TYPES.CFG)
        self.assertIn(member=', '.join(sorted(expected_types)),
                      container=raise_info.exception.message)

        with self.assertRaises(ValueError) as raise_info:
            ArakoonInstaller.get_unused_arakoon_clusters(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.CFG)
        self.assertIn(member=', '.join(sorted(expected_types)),
                      container=raise_info.exception.message)

        unused_abm_clusters = ArakoonInstaller.get_unused_arakoon_clusters(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.ABM)
        unused_fwk_clusters = ArakoonInstaller.get_unused_arakoon_clusters(cluster_type=ServiceType.ARAKOON_CLUSTER_TYPES.FWK)

        self.assertEqual(first=2,
                         second=len(unused_fwk_clusters))
        self.assertEqual(first=1,
                         second=len(unused_abm_clusters))

        for cluster_info in unused_abm_clusters:
            cl_name = cluster_info['cluster_name']
            self.assertIn(member=cl_name,
                          container=cluster_map)
            self.assertDictEqual(d1=json.loads(cluster_map[cl_name]['arakoon_client'].get(ArakoonInstaller.METADATA_KEY)),
                                 d2={'cluster_name': cl_name,
                                     'cluster_type': cluster_map[cl_name]['cluster_type'],
                                     'in_use': False,
                                     'internal': False})
