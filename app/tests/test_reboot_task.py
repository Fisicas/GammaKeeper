import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reboot_task


class RebootTaskTests(unittest.TestCase):
    SID = 'S-1-5-21-123-456-789-1001'
    LIVE_MODE = {'GAMMAKEEPER_TEST_MODE': '0', 'RADIALOG_TEST_MODE': '0'}

    def test_definition_limits_trigger_and_principal_to_current_user(self):
        run = Path(r'C:\Test data\測定 & calibration\run')
        pythonw = Path(r'C:\Science env\Scripts\pythonw.exe')
        definition = reboot_task._task_xml(run, self.SID, pythonw)
        task = ET.fromstring(definition)
        namespace = {'t': reboot_task.NAMESPACE}

        def value(xpath):
            return task.findtext(xpath, namespaces=namespace)

        self.assertEqual([node.tag.rsplit('}', 1)[-1] for node in task.find('t:Triggers', namespace)],
                         ['LogonTrigger'])
        self.assertEqual(value('t:Triggers/t:LogonTrigger/t:UserId'), self.SID)
        self.assertEqual(value('t:Triggers/t:LogonTrigger/t:Delay'), 'PT30S')
        self.assertEqual(value('t:Principals/t:Principal/t:UserId'), self.SID)
        self.assertEqual(value('t:Principals/t:Principal/t:LogonType'), 'InteractiveToken')
        self.assertEqual(value('t:Principals/t:Principal/t:RunLevel'), 'LeastPrivilege')
        self.assertEqual(value('t:Settings/t:MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(value('t:Settings/t:DisallowStartIfOnBatteries'), 'false')
        self.assertEqual(value('t:Settings/t:StopIfGoingOnBatteries'), 'false')
        self.assertEqual(value('t:Settings/t:Hidden'), 'true')
        self.assertEqual(value('t:Settings/t:ExecutionTimeLimit'), 'PT0S')
        self.assertEqual(value('t:Actions/t:Exec/t:Command'), str(pythonw))
        self.assertEqual(value('t:Actions/t:Exec/t:Arguments'), subprocess.list2cmdline(
            ['-X', 'utf8', str(reboot_task.ROOT / 'supervisor.py'), '--run', str(run), '--auto-resume']))
        self.assertEqual(value('t:Actions/t:Exec/t:WorkingDirectory'), str(reboot_task.ROOT))

        override = Path(r'C:\Isolated lab root')
        isolated = ET.fromstring(reboot_task._task_xml(run, self.SID, pythonw, override))
        self.assertEqual(isolated.findtext('t:Actions/t:Exec/t:Arguments', namespaces=namespace),
                         subprocess.list2cmdline(['-X', 'utf8', str(reboot_task.ROOT / 'supervisor.py'),
                                                  '--run', str(run), '--auto-resume', '--recovery-data-root',
                                                  str(override.resolve())]))

    def test_register_submits_xml_and_cleans_temporary_file_after_failure(self):
        submitted = []

        def pretend_command(binary, *args):
            if binary == 'whoami.exe':
                self.assertEqual(args, ('/user', '/fo', 'csv', '/nh'))
                return subprocess.CompletedProcess([], 0, f'"Lab User","{self.SID}"\n', '')
            self.assertEqual(binary, 'schtasks.exe')
            self.assertEqual(args[:4], ('/Create', '/TN', reboot_task.task_name(run), '/XML'))
            self.assertEqual(args[5:], ('/F', '/HRESULT'))
            xml = Path(args[4])
            self.assertIn(self.SID, xml.read_text(encoding='utf-16'))
            submitted.append(xml)
            return subprocess.CompletedProcess([], 0x80070005, '', 'Access is denied.')

        scratch = reboot_task.ROOT / 'validation' / 'reboot_task_temp'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory, patch.dict(os.environ, self.LIVE_MODE):
            pythonw = Path(directory) / 'pythonw.exe'
            pythonw.touch()
            run = Path(directory) / 'experiment'
            with (patch.object(reboot_task, 'PYTHONW', pythonw),
                  patch.object(reboot_task, '_command', pretend_command), patch.object(tempfile, 'tempdir', directory)):
                with self.assertRaisesRegex(OSError, 'Register GammaKeeper recovery task failed.*Access is denied'):
                    reboot_task.register(run)
            self.assertEqual(len(submitted), 1)
            self.assertFalse(submitted[0].exists())

    def test_remove_handles_a_missing_task_but_surfaces_access_denied(self):
        run = Path(r'C:\Test data\run')
        result = subprocess.CompletedProcess([], reboot_task.FILE_NOT_FOUND_HRESULT, '', '')
        with patch.dict(os.environ, self.LIVE_MODE), patch.object(reboot_task, '_command', return_value=result) as command:
            reboot_task.remove(run)
            command.assert_called_once_with('schtasks.exe', '/Delete', '/TN', reboot_task.task_name(run), '/F', '/HRESULT')
            result.returncode = 0x80070005
            result.stderr = 'Access is denied.'
            with self.assertRaisesRegex(OSError, 'Remove GammaKeeper recovery task failed.*Access is denied'):
                reboot_task.remove(run)

    def test_test_mode_never_touches_task_scheduler(self):
        for variable in self.LIVE_MODE:
            values = {**self.LIVE_MODE, variable: '1'}
            with self.subTest(variable=variable), patch.dict(os.environ, values), patch.object(reboot_task, '_command') as command:
                reboot_task.register('run')
                reboot_task.remove('run')
                command.assert_not_called()

    def test_task_name_distinguishes_same_folder_name_in_different_locations(self):
        first = reboot_task.task_name(r'C:\Run data\first\run')
        self.assertEqual(first, reboot_task.task_name(r'c:\RUN DATA\first\run'))
        self.assertNotEqual(first, reboot_task.task_name(r'C:\Run data\second\run'))


if __name__ == '__main__':
    unittest.main()
