# Copyright 2016 iNuron NV
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
VMachine module
"""
import time
from ovs.lib.helpers.decorators import log
from ovs.celery_run import celery
from ovs.dal.hybrids.pmachine import PMachine
from ovs.dal.hybrids.vmachine import VMachine
from ovs.dal.hybrids.vdisk import VDisk
from ovs.dal.hybrids.storagerouter import StorageRouter
from ovs.dal.lists.vmachinelist import VMachineList
from ovs.dal.lists.pmachinelist import PMachineList
from ovs.dal.lists.vdisklist import VDiskList
from ovs.dal.lists.storagerouterlist import StorageRouterList
from ovs.dal.lists.storagedriverlist import StorageDriverList
from ovs.dal.lists.mgmtcenterlist import MgmtCenterList
from ovs.dal.lists.vpoollist import VPoolList
from ovs.extensions.hypervisor.factory import Factory
from ovs.lib.vdisk import VDiskController
from ovs.lib.messaging import MessageController
from ovs.lib.mdsservice import MDSServiceController
from ovs.log.logHandler import LogHandler
from ovs.extensions.generic.volatilemutex import volatile_mutex


class VMachineController(object):
    """
    Contains all BLL related to VMachines
    """
    _logger = LogHandler.get('lib', name='vmachine')

    @staticmethod
    @celery.task(name='ovs.machine.create_multiple_from_template')
    def create_multiple_from_template(name, machineguid, pmachineguids, start, amount, description=None):
        """
        Create multiple machines from a template
        :param name: Name of the machines to be created
        :param machineguid: Guid of the template machine
        :param pmachineguids: Physical machine guids on which to spread the created the machines
        :param start: Start to count
        :param amount: Amount to deploy
        :param description: Description for the machines
        :return: None
        """
        pmachine_pointer = 0
        for i in xrange(start, start + amount):
            new_name = name if amount == 1 else '{0}-{1}'.format(name, i)
            pmachineguid = pmachineguids[pmachine_pointer]
            pmachine_pointer += 1
            if pmachine_pointer >= len(pmachineguids):
                pmachine_pointer = 0
            VMachineController.create_from_template(name=new_name,
                                                    machineguid=machineguid,
                                                    pmachineguid=pmachineguid,
                                                    description=description)

    @staticmethod
    @celery.task(name='ovs.machine.create_from_template')
    def create_from_template(name, machineguid, pmachineguid, description=None):
        """
        Create a new vmachine using an existing vmachine template

        :param machineguid: guid of the template vmachine
        :param name: name of new vmachine
        :param pmachineguid: guid of hypervisor to create new vmachine on
        :param description: Description for the machine
        :return: guid of the newly created vmachine | False on any failure
        """

        template_vm = VMachine(machineguid)
        if not template_vm.is_vtemplate:
            return False

        target_pm = PMachine(pmachineguid)
        target_hypervisor = Factory.get(target_pm)

        storagerouters = [sr for sr in StorageRouterList.get_storagerouters() if sr.pmachine_guid == target_pm.guid]
        if len(storagerouters) == 1:
            target_storagerouter = storagerouters[0]
        else:
            raise ValueError('Pmachine {0} has no StorageRouter assigned to it'.format(pmachineguid))
        routing_key = "sr.{0}".format(target_storagerouter.machine_id)

        vpool = None
        vpool_guids = set()
        if template_vm.vpool is not None:
            vpool = template_vm.vpool
            vpool_guids.add(vpool.guid)
        for disk in template_vm.vdisks:
            vpool = disk.vpool
            vpool_guids.add(vpool.guid)
        if len(vpool_guids) != 1:
            raise RuntimeError('Only 1 vpool supported on template disk(s) - {0} found!'.format(len(vpool_guids)))

        if not template_vm.pmachine.hvtype == target_pm.hvtype:
            raise RuntimeError('Source and target hypervisor not identical')

        # Currently, only one vPool is supported, so we can just use whatever the `vpool` variable above
        # was set to as 'the' vPool for the code below. This obviously will have to change once vPool mixes
        # are supported.

        target_storagedriver = None
        source_storagedriver = None
        for vpool_storagedriver in vpool.storagedrivers:
            if vpool_storagedriver.storagerouter.pmachine_guid == target_pm.guid:
                target_storagedriver = vpool_storagedriver
            if vpool_storagedriver.storagerouter.pmachine_guid == template_vm.pmachine_guid:
                source_storagedriver = vpool_storagedriver
        if target_storagedriver is None:
            raise RuntimeError('Volume not served on target hypervisor')

        source_hv = Factory.get(template_vm.pmachine)
        target_hv = Factory.get(target_pm)
        if not source_hv.is_datastore_available(source_storagedriver.storage_ip, source_storagedriver.mountpoint):
            raise RuntimeError('Datastore unavailable on source hypervisor')
        if not target_hv.is_datastore_available(target_storagedriver.storage_ip, target_storagedriver.mountpoint):
            raise RuntimeError('Datastore unavailable on target hypervisor')

        source_vm = source_hv.get_vm_object(template_vm.hypervisor_id)
        if not source_vm:
            raise RuntimeError('VM with key reference {0} not found'.format(template_vm.hypervisor_id))

        name_duplicates = VMachineList.get_vmachine_by_name(name)
        if name_duplicates is not None and len(name_duplicates) > 0:
            raise RuntimeError('A vMachine with name {0} already exists'.format(name))

        vm_path = target_hypervisor.get_vmachine_path(name, target_storagedriver.storagerouter.machine_id)

        new_vm = VMachine()
        new_vm.copy(template_vm)
        new_vm.hypervisor_id = ''
        new_vm.vpool = template_vm.vpool
        new_vm.pmachine = target_pm
        new_vm.name = name
        new_vm.description = description
        new_vm.is_vtemplate = False
        new_vm.devicename = target_hypervisor.clean_vmachine_filename(vm_path)
        new_vm.status = 'CREATED'
        new_vm.save()

        storagedrivers = [storagedriver for storagedriver in vpool.storagedrivers if storagedriver.storagerouter.pmachine_guid == new_vm.pmachine_guid]
        if len(storagedrivers) == 0:
            raise RuntimeError('Cannot find Storage Driver serving {0} on {1}'.format(vpool.name, new_vm.pmachine.name))

        disks = []
        disks_by_order = sorted(template_vm.vdisks, key=lambda x: x.order)
        for disk in disks_by_order:
            try:
                prefix = '{0}-clone'.format(disk.name)
                result = VDiskController.create_from_template(
                    diskguid=disk.guid,
                    devicename=prefix,
                    pmachineguid=target_pm.guid,
                    machinename=new_vm.name,
                    machineguid=new_vm.guid
                )
                disks.append(result)
                VMachineController._logger.debug('Disk appended: {0}'.format(result))
            except Exception as exception:
                VMachineController._logger.error('Creation of disk {0} failed: {1}'.format(disk.name, str(exception)), print_msg=True)
                VMachineController.delete.s(machineguid=new_vm.guid).apply_async(routing_key = routing_key)
                raise

        try:
            result = target_hv.create_vm_from_template(
                name, source_vm, disks, target_storagedriver.storage_ip, target_storagedriver.mountpoint, wait=True
            )
        except Exception as exception:
            VMachineController._logger.error('Creation of vm {0} on hypervisor failed: {1}'.format(new_vm.name, str(exception)), print_msg=True)
            VMachineController.delete.s(machineguid=new_vm.guid).apply_async(routing_key = routing_key)
            raise

        new_vm.hypervisor_id = result
        new_vm.status = 'SYNC'
        new_vm.save()
        return new_vm.guid

    @staticmethod
    @celery.task(name='ovs.machine.clone')
    def clone(machineguid, timestamp, name):
        """
        Clone a vmachine using the disk snapshot based on a snapshot timestamp

        @param machineguid: guid of the machine to clone
        @param timestamp: timestamp of the disk snapshots to use for the clone
        @param name: name for the new machine
        """
        machine = VMachine(machineguid)
        timestamp = str(timestamp)
        if timestamp not in (snap['timestamp'] for snap in machine.snapshots):
            raise RuntimeError('Invalid timestamp provided, not a valid snapshot of this vmachine.')

        vpool = None
        storagerouter = None
        if machine.pmachine is not None and machine.pmachine.hvtype == 'VMWARE':
            for vdisk in machine.vdisks:
                if vdisk.vpool is not None:
                    vpool = vdisk.vpool
                    break
        for vdisk in machine.vdisks:
            if vdisk.storagerouter_guid:
                storagerouter = StorageRouter(vdisk.storagerouter_guid)
                break
        hv = Factory.get(machine.pmachine)
        vm_path = hv.get_vmachine_path(name, storagerouter.machine_id if storagerouter is not None else '')
        # mutex in sync_with_hypervisor uses "None" for KVM hvtype
        mutex = volatile_mutex('{0}_{1}'.format(hv.clean_vmachine_filename(vm_path), vpool.guid if vpool is not None else 'none'))

        disks = {}
        for snapshot in machine.snapshots:
            if snapshot['timestamp'] == timestamp:
                for diskguid, snapshotguid in snapshot['snapshots'].iteritems():
                    disks[diskguid] = snapshotguid

        try:
            mutex.acquire(wait=120)
            new_machine = VMachine()
            new_machine.copy(machine)
            new_machine.name = name
            new_machine.devicename = hv.clean_vmachine_filename(vm_path)
            new_machine.pmachine = machine.pmachine
            new_machine.save()
        finally:
            mutex.release()

        new_disk_guids = []
        vm_disks = []
        mountpoint = None
        disks_by_order = sorted(machine.vdisks, key=lambda x: x.order)
        try:
            for currentDisk in disks_by_order:
                if machine.is_vtemplate and currentDisk.templatesnapshot:
                    snapshotid = currentDisk.templatesnapshot
                else:
                    snapshotid = disks[currentDisk.guid]
                prefix = '%s-clone' % currentDisk.name

                result = VDiskController.clone(diskguid=currentDisk.guid,
                                               snapshotid=snapshotid,
                                               devicename=prefix,
                                               pmachineguid=new_machine.pmachine_guid,
                                               machinename=new_machine.name,
                                               machineguid=new_machine.guid)
                new_disk_guids.append(result['diskguid'])
                mountpoint = StorageDriverList.get_by_storagedriver_id(currentDisk.storagedriver_id).mountpoint
                vm_disks.append(result)
        except Exception as ex:
            VMachineController._logger.error('Failed to clone disks. {0}'.format(ex))
            VMachineController.delete(machineguid=new_machine.guid)
            raise

        try:
            result = hv.clone_vm(machine.hypervisor_id, name, vm_disks, mountpoint)
        except Exception as ex:
            VMachineController._logger.error('Failed to clone vm. {0}'.format(ex))
            VMachineController.delete(machineguid=new_machine.guid)
            raise

        try:
            mutex.acquire(wait=120)
            new_machine.hypervisor_id = result
            new_machine.save()
        finally:
            mutex.release()
        return new_machine.guid

    @staticmethod
    @celery.task(name='ovs.machine.delete')
    def delete(machineguid):
        """
        Delete a vmachine

        @param machineguid: guid of the machine
        """
        machine = VMachine(machineguid)
        storagedriver_mountpoint, storagedriver_storage_ip = None, None

        try:
            storagedriver = [storagedriver for storagedriver in machine.vpool.storagedrivers if storagedriver.storagerouter.pmachine_guid == machine.pmachine_guid][0]
            storagedriver_mountpoint = storagedriver.mountpoint
            storagedriver_storage_ip = storagedriver.storage_ip
        except Exception as ex:
            VMachineController._logger.debug('No mountpoint info could be retrieved. Reason: {0}'.format(str(ex)))
            storagedriver_mountpoint = None

        disks_info = []
        for vd in machine.vdisks:
            for storagedriver in vd.vpool.storagedrivers:
                if storagedriver.storagerouter.pmachine_guid == machine.pmachine_guid:
                    disks_info.append((storagedriver.mountpoint, vd.devicename))
        if machine.pmachine:  # Allow hypervisor id node, lookup strategy is hypervisor dependent
            try:
                hypervisor_id = machine.hypervisor_id
                if machine.pmachine.hvtype == 'KVM':
                    hypervisor_id = machine.name  # On KVM we can lookup the machine by name, not by id

                hv = Factory.get(machine.pmachine)
                hv.delete_vm(hypervisor_id, storagedriver_mountpoint, storagedriver_storage_ip, machine.devicename, disks_info, True)
            except Exception as exception:
                VMachineController._logger.error('Deletion of vm on hypervisor failed: {0}'.format(str(exception)), print_msg=True)

        for disk in machine.vdisks:
            VMachineController._logger.debug('Deleting disk {0} with guid: {1}'.format(disk.name, disk.guid))
            for junction in disk.mds_services:
                junction.delete()
            disk.delete()
        VMachineController._logger.debug('Deleting vmachine {0} with guid {1}'.format(machine.name, machine.guid))
        machine.delete()

    @staticmethod
    @celery.task(name='ovs.machine.delete_from_voldrv')
    @log('VOLUMEDRIVER_TASK')
    def delete_from_voldrv(name, storagedriver_id):
        """
        This method will delete a vmachine based on the name of the vmx given
        :param name: Name of the vmx
        :param storagedriver_id: Storage Driver hosting the vmachine
        """
        pmachine = PMachineList.get_by_storagedriver_id(storagedriver_id)
        if pmachine.hvtype not in ['VMWARE', 'KVM']:
            return

        hypervisor = Factory.get(pmachine)
        name = hypervisor.clean_vmachine_filename(name)
        if pmachine.hvtype == 'VMWARE':
            storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
            vpool = storagedriver.vpool
        else:
            vpool = None
        vm = VMachineList.get_by_devicename_and_vpool(name, vpool)
        if vm is not None:
            MessageController.fire(MessageController.Type.EVENT, {'type': 'vmachine_deleted',
                                                                  'metadata': {'name': vm.name}})
            vm.delete(abandon=['vdisks'])

    @staticmethod
    @celery.task(name='ovs.machine.rename_from_voldrv')
    @log('VOLUMEDRIVER_TASK')
    def rename_from_voldrv(old_name, new_name, storagedriver_id):
        """
        This machine will handle the rename of a vmx file
        :param old_name: Old name of vmx
        :param new_name: New name for the vmx
        :param storagedriver_id: Storage Driver hosting the vmachine
        """
        pmachine = PMachineList.get_by_storagedriver_id(storagedriver_id)
        if pmachine.hvtype not in ['VMWARE', 'KVM']:
            return

        hypervisor = Factory.get(pmachine)
        if pmachine.hvtype == 'VMWARE':
            storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
            vpool = storagedriver.vpool
        else:
            vpool = None

        old_name = hypervisor.clean_vmachine_filename(old_name)
        new_name = hypervisor.clean_vmachine_filename(new_name)
        scenario = hypervisor.get_rename_scenario(old_name, new_name)
        if scenario == 'RENAME':
            # Most likely a change from path. Updating path
            vm = VMachineList.get_by_devicename_and_vpool(old_name, vpool)
            if vm is not None:
                vm.devicename = new_name
                vm.save()
        elif scenario == 'UPDATE':
            vm = VMachineList.get_by_devicename_and_vpool(new_name, vpool)
            if vm is None:
                # The vMachine doesn't seem to exist, so it's likely the create didn't came trough
                # Let's create it anyway
                VMachineController.update_from_voldrv(new_name, storagedriver_id=storagedriver_id)
            vm = VMachineList.get_by_devicename_and_vpool(new_name, vpool)
            if vm is None:
                raise RuntimeError('Could not create vMachine on rename. Aborting.')
            try:
                VMachineController.sync_with_hypervisor(vm.guid, storagedriver_id=storagedriver_id)
                vm.status = 'SYNC'
            except:
                vm.status = 'SYNC_NOK'
            vm.save()

    @staticmethod
    @celery.task(name='ovs.machine.set_as_template')
    def set_as_template(machineguid):
        """
        Set a vmachine as template

        @param machineguid: guid of the machine
        @return: vmachine template conversion successful: True|False
        """
        # Do some magic on the storage layer?
        # This is most likely required as extra security measure
        # Suppose the template is set back to a real machine
        # it can be deleted within vmware which should be blocked.
        # This might also require a storagerouter internal check
        # to be implemented to discourage volumes from being deleted
        # when clones were made from it.

        vmachine = VMachine(machineguid)
        if vmachine.hypervisor_status == 'RUNNING':
            raise RuntimeError('vMachine {0} may not be running to set it as vTemplate'.format(vmachine.name))

        for disk in vmachine.vdisks:
            VDiskController.set_as_template(diskguid=disk.guid)

        vmachine.is_vtemplate = True
        vmachine.invalidate_dynamics(['snapshots'])
        vmachine.save()

    @staticmethod
    @celery.task(name='ovs.machine.rollback')
    def rollback(machineguid, timestamp):
        """
        Rolls back a VM based on a given disk snapshot timestamp
        :param machineguid: Guid of the machine to rollback
        :param timestamp: Timestamp to rollback to
        """
        vmachine = VMachine(machineguid)
        if vmachine.hypervisor_status == 'RUNNING':
            raise RuntimeError('vMachine {0} may not be running to set it as vTemplate'.format(
                vmachine.name
            ))

        snapshots = [snap for snap in vmachine.snapshots if snap['timestamp'] == timestamp]
        if not snapshots:
            raise ValueError('No vmachine snapshots found for timestamp {0}'.format(timestamp))

        for disk in vmachine.vdisks:
            VDiskController.rollback(diskguid=disk.guid,
                                     timestamp=timestamp)

        vmachine.invalidate_dynamics(['snapshots'])

    @staticmethod
    @celery.task(name='ovs.machine.snapshot')
    def snapshot(machineguid, label=None, is_consistent=False, timestamp=None, is_automatic=False, is_sticky=False):
        """
        Snapshot VMachine disks

        :param machineguid: guid of the machine
        :param label: label to give the snapshots
        :param is_consistent: flag indicating the snapshot was consistent or not
        :param timestamp: override timestamp, if required. Should be a unix timestamp
        :param is_automatic: Flag to determine automated snapshots
        :param is_sticky: Flag indicating the snapshot is not to be deleted automatically
        """

        timestamp = timestamp if timestamp is not None else time.time()
        timestamp = str(int(float(timestamp)))

        if is_automatic is True and is_sticky is True:
            raise ValueError('Snapshot {0} cannot be both automatic and sticky'.format(label))
        metadata = {'label': label,
                    'is_consistent': is_consistent,
                    'timestamp': timestamp,
                    'machineguid': machineguid,
                    'is_automatic': is_automatic,
                    'is_sticky': is_sticky}
        machine = VMachine(machineguid)

        # @todo: we now skip creating a snapshot when a vmachine's disks
        #        is missing a mandatory property: volume_id
        #        sub-task will now raise an exception earlier in the workflow
        for disk in machine.vdisks:
            if not disk.volume_id:
                message = 'Missing volume_id on disk {0} - unable to create snapshot for vm {1}'.format(
                    disk.guid, machine.guid
                )
                VMachineController._logger.info('Error: {0}'.format(message))
                raise RuntimeError(message)

        snapshots = {}
        success = True
        for disk in machine.vdisks:
            try:
                snapshots[disk.guid] = VDiskController.create_snapshot(diskguid=disk.guid,
                                                                       metadata=metadata)
            except Exception as ex:
                VMachineController._logger.info('Error taking snapshot of disk {0}: {1}'.format(disk.name, str(ex)))
                success = False
                for diskguid, snapshotid in snapshots.iteritems():
                    VDiskController.delete_snapshot(diskguid=diskguid,
                                                    snapshotid=snapshotid)
        VMachineController._logger.info('Create snapshot for vMachine {0}: {1}'.format(
            machine.name, 'Success' if success else 'Failure'
        ))
        machine.invalidate_dynamics(['snapshots'])
        if not success:
            raise RuntimeError('Failed to snapshot vMachine {0}'.format(machine.name))

    @staticmethod
    @celery.task(name='ovs.machine.delete_snapshot')
    def delete_snapshot(vmachineguid, timestamp):
        """
        Remove a snapshot from the vmachine
        @param vmachineguid: Guid of the virtual machine
        @param timestamp: timestamp of the snapshot
        """
        vmachine = VMachine(vmachineguid)
        vmachine_snapshots = [snap for snap in vmachine.snapshots if snap['timestamp'] == str(timestamp)]
        if len(vmachine_snapshots) != 1:
            raise RuntimeError('Snapshot {0} does not belong to vmachine {1}'.format(timestamp, vmachine.name))
        vmachine_snapshot = vmachine_snapshots[0]
        VMachineController._logger.info('Deleting snapshot {0} from vmachine {1}'.format(timestamp, vmachine.name))
        for diskguid, snapshotid in vmachine_snapshot['snapshots'].items():
            VDiskController.delete_snapshot(diskguid, snapshotid)
        VMachineController._logger.info('Deleted snapshot {0}'.format(timestamp))
        vmachine.invalidate_dynamics(['snapshots'])

    @staticmethod
    @celery.task(name='ovs.machine.sync_with_hypervisor')
    @log('VOLUMEDRIVER_TASK')
    def sync_with_hypervisor(vmachineguid, storagedriver_id=None):
        """
        Updates a given vmachine with data retrieved from a given pmachine
        :param vmachineguid: Guid of the virtual machine
        :param storagedriver_id: Storage Driver hosting the vmachine
        """
        try:
            vmachine = VMachine(vmachineguid)
        except Exception as ex:
            VMachineController._logger.info('Cannot get VMachine object: {0}'.format(str(ex)))
            raise

        vm_object = None
        if vmachine.pmachine.mgmtcenter and storagedriver_id is not None and vmachine.devicename is not None:
            try:
                mgmt_center = Factory.get_mgmtcenter(vmachine.pmachine)
                storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
                VMachineController._logger.info('Syncing vMachine (name {0}) with Management center {1}'.format(vmachine.name, vmachine.pmachine.mgmtcenter.name))
                vm_object = mgmt_center.get_vm_agnostic_object(devicename=vmachine.devicename,
                                                               ip=storagedriver.storage_ip,
                                                               mountpoint=storagedriver.mountpoint)
            except Exception as ex:
                VMachineController._logger.info('Error while fetching vMachine info from management center: {0}'.format(str(ex)))

        if vm_object is None and storagedriver_id is None and vmachine.hypervisor_id is not None and vmachine.pmachine is not None:
            try:
                # Only the vmachine was received, so base the sync on hypervisor id and pmachine
                hypervisor = Factory.get(vmachine.pmachine)
                VMachineController._logger.info('Syncing vMachine (name {0})'.format(vmachine.name))
                vm_object = hypervisor.get_vm_agnostic_object(vmid=vmachine.hypervisor_id)
            except Exception as ex:
                VMachineController._logger.info('Error while fetching vMachine info from hypervisor: {0}'.format(str(ex)))

        if vm_object is None and storagedriver_id is not None and vmachine.devicename is not None:
            try:
                # Storage Driver id was given, using the devicename instead (to allow hypervisor id updates
                # which can be caused by re-adding a vm to the inventory)
                pmachine = PMachineList.get_by_storagedriver_id(storagedriver_id)
                storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
                hypervisor = Factory.get(pmachine)
                if not hypervisor.file_exists(storagedriver, hypervisor.clean_vmachine_filename(vmachine.devicename)):
                    return
                vmachine.pmachine = pmachine
                vmachine.save()

                VMachineController._logger.info('Syncing vMachine (device {0}, ip {1}, mountpoint {2})'.format(vmachine.devicename,
                                                                                                               storagedriver.storage_ip,
                                                                                                               storagedriver.mountpoint))
                vm_object = hypervisor.get_vm_object_by_devicename(devicename=vmachine.devicename,
                                                                   ip=storagedriver.storage_ip,
                                                                   mountpoint=storagedriver.mountpoint)
            except Exception as ex:
                VMachineController._logger.info('Error while fetching vMachine info from hypervisor using devicename: {0}'.format(str(ex)))

        if vm_object is None:
            message = 'Not enough information to sync vmachine'
            VMachineController._logger.info('Error: {0}'.format(message))
            raise RuntimeError(message)

        VMachineController.update_vmachine_config(vmachine, vm_object)

    @staticmethod
    @celery.task(name='ovs.machine.update_from_voldrv')
    @log('VOLUMEDRIVER_TASK')
    def update_from_voldrv(name, storagedriver_id):
        """
        This method will update/create a vmachine based on a given vmx/xml file
        :param name: Name of the vmx
        :param storagedriver_id: Storage Driver hosting the vmachine
        """

        pmachine = PMachineList.get_by_storagedriver_id(storagedriver_id)
        if pmachine.hvtype not in ['VMWARE', 'KVM']:
            return

        hypervisor = Factory.get(pmachine)
        name = hypervisor.clean_vmachine_filename(name)
        storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
        vpool = storagedriver.vpool
        machine_ids = [storagedriver.storagerouter.machine_id for storagedriver in vpool.storagedrivers]

        if hypervisor.should_process(name, machine_ids=machine_ids):
            if pmachine.hvtype == 'VMWARE':
                storagedriver = StorageDriverList.get_by_storagedriver_id(storagedriver_id)
                vpool = storagedriver.vpool
            else:
                vpool = None
            pmachine = PMachineList.get_by_storagedriver_id(storagedriver_id)
            mutex = volatile_mutex('{0}_{1}'.format(name, vpool.guid if vpool is not None else 'none'))
            try:
                mutex.acquire(wait=120)
                limit = 5
                exists = hypervisor.file_exists(storagedriver, name)
                while limit > 0 and exists is False:
                    time.sleep(1)
                    exists = hypervisor.file_exists(storagedriver, name)
                    limit -= 1
                if exists is False:
                    VMachineController._logger.info('Could not locate vmachine with name {0} on vpool {1}'.format(name, vpool.name))
                    vmachine = VMachineList.get_by_devicename_and_vpool(name, vpool)
                    if vmachine is not None:
                        VMachineController.delete_from_voldrv(name, storagedriver_id=storagedriver_id)
                    return
            finally:
                mutex.release()
            try:
                mutex.acquire(wait=5)
                vmachine = VMachineList.get_by_devicename_and_vpool(name, vpool)
                if not vmachine:
                    vmachines = VMachineList.get_vmachine_by_name(name)
                    if vmachines is not None:
                        vmachine = vmachines[0]
                if not vmachine:
                    vmachine = VMachine()
                    vmachine.vpool = vpool
                    vmachine.pmachine = pmachine
                    vmachine.status = 'CREATED'
                vmachine.devicename = name
                vmachine.save()
            finally:
                mutex.release()

            if pmachine.hvtype == 'KVM':
                try:
                    mutex.acquire(wait=120)
                    VMachineController.sync_with_hypervisor(vmachine.guid, storagedriver_id=storagedriver_id)
                    vmachine.status = 'SYNC'
                except:
                    vmachine.status = 'SYNC_NOK'
                finally:
                    mutex.release()
                vmachine.save()
        else:
            VMachineController._logger.info('Ignored invalid file {0}'.format(name))

    @staticmethod
    @celery.task(name='ovs.machine.update_vmachine_config')
    def update_vmachine_config(vmachine, vm_object, pmachine=None):
        """
        Update a vMachine configuration with a given vMachine configuration
        :param vmachine: Virtual Machine to update
        :param vm_object: New virtual machine info
        :param pmachine: Physical machine of the virtual machine
        """
        try:
            vdisks_synced = 0
            if vmachine.name is None:
                MessageController.fire(MessageController.Type.EVENT,
                                       {'type': 'vmachine_created',
                                        'metadata': {'name': vm_object['name']}})
            elif vmachine.name != vm_object['name']:
                MessageController.fire(MessageController.Type.EVENT,
                                       {'type': 'vmachine_renamed',
                                        'metadata': {'old_name': vmachine.name,
                                                     'new_name': vm_object['name']}})
            if pmachine is not None:
                vmachine.pmachine = pmachine
            vmachine.name = vm_object['name']
            vmachine.hypervisor_id = vm_object['id']
            vmachine.devicename = vm_object['backing']['filename']
            vmachine.save()
            # Updating and linking disks
            storagedrivers = StorageDriverList.get_storagedrivers()
            datastores = dict([('{0}:{1}'.format(storagedriver.storage_ip, storagedriver.mountpoint), storagedriver) for storagedriver in storagedrivers])
            vdisk_guids = []
            mutex = volatile_mutex('{0}_{1}'.format(vmachine.name, vmachine.devicename))
            for disk in vm_object['disks']:
                ensure_safety = False
                if disk['datastore'] in vm_object['datastores']:
                    datastore = vm_object['datastores'][disk['datastore']]
                    if datastore in datastores:
                        try:
                            mutex.acquire(wait=10)
                            vdisk = VDiskList.get_by_devicename_and_vpool(disk['filename'], datastores[datastore].vpool)
                            if vdisk is None:
                                # The disk couldn't be located, but is in our datastore. We might be in a recovery scenario
                                vdisk = VDisk()
                                vdisk.vpool = datastores[datastore].vpool
                                vdisk.reload_client()
                                vdisk.devicename = disk['filename']
                                vdisk.volume_id = vdisk.storagedriver_client.get_volume_id(str(disk['backingfilename']))
                                vdisk.size = vdisk.info['volume_size']
                                vdisk.metadata = {'lba_size': vdisk.info['lba_size'],
                                                  'cluster_multiplier': vdisk.info['cluster_multiplier']}
                                # Create the disk in a locked context, but don't execute long running-task in same context
                                vdisk.save()
                                ensure_safety = True
                        finally:
                            mutex.release()
                        if ensure_safety:
                            MDSServiceController.ensure_safety(vdisk)
                            VDiskController.dtl_checkup(vdisk_guid=vdisk.guid)

                        # Update the disk with information from the hypervisor
                        if vdisk.vmachine is None:
                            MessageController.fire(MessageController.Type.EVENT,
                                                   {'type': 'vdisk_attached',
                                                    'metadata': {'vmachine_name': vmachine.name,
                                                                 'vdisk_name': disk['name']}})
                        vdisk.vmachine = vmachine
                        vdisk.name = disk['name']
                        vdisk.order = disk['order']
                        vdisk.save()
                        vdisk_guids.append(vdisk.guid)
                        vdisks_synced += 1

            for vdisk in vmachine.vdisks:
                if vdisk.guid not in vdisk_guids:
                    MessageController.fire(MessageController.Type.EVENT,
                                           {'type': 'vdisk_detached',
                                            'metadata': {'vmachine_name': vmachine.name,
                                                         'vdisk_name': vdisk.name}})
                    vdisk.vmachine = None
                    vdisk.save()

            VMachineController._logger.info('Updating vMachine finished (name {0}, {1} vdisks (re)linked)'.format(
                vmachine.name, vdisks_synced
            ))
        except Exception as ex:
            VMachineController._logger.info('Error during vMachine update: {0}'.format(str(ex)))
            raise

    @staticmethod
    @celery.task(name='ovs.machine.update_vmachine_name')
    def update_vmachine_name(instance_id, old_name, new_name):
        """
        Update a vMachine name: find vmachine by management center instance id, set new name
        :param instance_id: ID for the virtual machine known by management center
        :param old_name: Old name of the virtual machine
        :param new_name: New name for the virtual machine
        """
        vmachine = None
        for mgmt_center in MgmtCenterList.get_mgmtcenters():
            mgmt = Factory.get_mgmtcenter(mgmt_center = mgmt_center)
            try:
                machine_info = mgmt.get_vmachine_device_info(instance_id)
                file_name = machine_info['file_name']
                host_name = machine_info['host_name']
                vpool_name = machine_info['vpool_name']
                storage_router = StorageRouterList.get_by_name(host_name)
                machine_id = storage_router.machine_id
                device_name = '{0}/{1}'.format(machine_id, file_name)
                vp = VPoolList.get_vpool_by_name(vpool_name)
                vmachine = VMachineList.get_by_devicename_and_vpool(device_name, vp)
                if vmachine:
                    break
                vmachine = VMachineList.get_by_devicename_and_vpool(device_name, None)
                if vmachine:
                    break
            except Exception as ex:
                VMachineController._logger.info('Trying to get mgmt center failed for vmachine {0}. {1}'.format(old_name, ex))
        if not vmachine:
            VMachineController._logger.error('No vmachine found for name {0}'.format(old_name))
            return

        vpool = vmachine.vpool
        mutex = volatile_mutex('{0}_{1}'.format(old_name, vpool.guid if vpool is not None else 'none'))
        try:
            mutex.acquire(wait=5)
            vmachine.name = new_name
            vmachine.save()
        finally:
            mutex.release()
