# Copyright 2014 Open vStorage NV
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
StorageDriver module
"""
from ovs.dal.dataobject import DataObject
from ovs.dal.structures import Property, Relation, Dynamic
from ovs.dal.hybrids.vpool import VPool
from ovs.dal.hybrids.storagerouter import StorageRouter
from ovs.extensions.storageserver.storagedriver import StorageDriverClient

import time


class StorageDriver(DataObject):
    """
    The StorageDriver class represents a Storage Driver. A Storage Driver is an application
    on a Storage Router to which the vDisks connect. The Storage Driver is the gateway to the Storage Backend.
    """
    __properties = [Property('name', str, doc='Name of the Storage Driver.'),
                    Property('description', str, mandatory=False, doc='Description of the Storage Driver.'),
                    Property('ports', list, doc='Ports on which the Storage Driver is listening [mgmt, xmlrpc, dtl].'),
                    Property('cluster_ip', str, doc='IP address on which the Storage Driver is listening.'),
                    Property('storage_ip', str, doc='IP address on which the vpool is shared to hypervisor'),
                    Property('storagedriver_id', str, doc='ID of the Storage Driver as known by the Storage Drivers.'),
                    Property('mountpoint', str, doc='Mountpoint from which the Storage Driver serves data'),
                    Property('startup_counter', int, default=0, doc='StorageDriver startup counter')]
    __relations = [Relation('vpool', VPool, 'storagedrivers'),
                   Relation('storagerouter', StorageRouter, 'storagedrivers')]
    __dynamics = [Dynamic('status', str, 30),
                  Dynamic('statistics', dict, 0),
                  Dynamic('stored_data', int, 60),
                  Dynamic('mountpoints', dict, 3600),
                  Dynamic('paths', dict, 3600)]

    def _status(self):
        """
        Fetches the Status of the Storage Driver.
        """
        _ = self
        return None

    def _statistics(self):
        """
        Aggregates the Statistics (IOPS, Bandwidth, ...) of the vDisks connected to the Storage Driver.
        """
        client = StorageDriverClient()
        vdiskstatsdict = {}
        for key in client.STAT_KEYS:
            vdiskstatsdict[key] = 0
            vdiskstatsdict['{0}_ps'.format(key)] = 0
        if self.vpool is not None:
            for disk in self.vpool.vdisks:
                if disk.storagedriver_id == self.storagedriver_id:
                    for key, value in disk.statistics.iteritems():
                        if key != 'timestamp':
                            vdiskstatsdict[key] += value
        vdiskstatsdict['timestamp'] = time.time()
        return vdiskstatsdict

    def _stored_data(self):
        """
        Aggregates the Stored Data in Bytes of the vDisks connected to the Storage Driver.
        """
        if self.vpool is not None:
            return sum([disk.info['stored'] for disk in self.vpool.vdisks])
        return 0

    def _mountpoints(self):
        """
        Returns all mountpoint used by this storagedriver
        """
        result = dict()
        for sd_partition in self.partitions:
            if sd_partition:
                if sd_partition.usage not in result:
                    result[sd_partition.usage] = list()
                else:
                    result[sd_partition.usage].append(sd_partition.mountpoint)
        return result

    def _paths(self):
        """
        Returns all actual paths used by this storagedriver
        """
        result = dict()
        for sd_partition in self.partitions:
            if sd_partition:
                if sd_partition.usage not in result:
                    result[sd_partition.usage] = list()
                else:
                    result[sd_partition.usage].append(sd_partition.path)
        return result
