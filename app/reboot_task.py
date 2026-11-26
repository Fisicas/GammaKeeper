"""Register one Windows sign-in recovery task for an active GammaKeeper run."""
import csv
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from xml.etree import ElementTree as ET

from runtime import PYTHONW


ROOT = Path(__file__).resolve().parent
NAMESPACE = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
FILE_NOT_FOUND_HRESULT = 0x80070002


def task_name(run):
    path = os.path.normcase(os.path.abspath(run))
    tag = hashlib.sha256(os.fsencode(path)).hexdigest()[:24]
    return f'GammaKeeper Reboot Recovery {tag}'


def _test_mode():
    return '1' in (os.environ.get('GAMMAKEEPER_TEST_MODE'), os.environ.get('RADIALOG_TEST_MODE'))


def _command(executable, *arguments):
    binary = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / executable
    try:
        return subprocess.run([str(binary), *arguments], capture_output=True, text=True, errors='replace',
                              timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise OSError(f'Could not run {executable}: {error}') from error


def _failure(action, result):
    detail = (result.stderr or result.stdout).strip()
    return OSError(f'{action} failed (exit 0x{result.returncode & 0xffffffff:08X}): {detail}')


def _user_sid():
    result = _command('whoami.exe', '/user', '/fo', 'csv', '/nh')
    if result.returncode:
        raise _failure('Read Windows user SID', result)
    for row in csv.reader(result.stdout.splitlines()):
        if row and re.fullmatch(r'S-\d+(?:-\d+)+', row[-1]):
            return row[-1]
    raise OSError('The signed-in Windows user SID could not be read')


def _task_xml(run, sid, pythonw, data_root=None):
    task = ET.Element('Task', {'version': '1.2', 'xmlns': NAMESPACE})
    info = ET.SubElement(task, 'RegistrationInfo')
    ET.SubElement(info, 'Description').text = 'Resume the interrupted GammaKeeper run at Windows sign-in.'

    logon = ET.SubElement(ET.SubElement(task, 'Triggers'), 'LogonTrigger')
    ET.SubElement(logon, 'ExecutionTimeLimit').text = 'PT0S'
    ET.SubElement(logon, 'UserId').text = sid
    ET.SubElement(logon, 'Delay').text = 'PT30S'

    principal = ET.SubElement(ET.SubElement(task, 'Principals'), 'Principal', id='Operator')
    ET.SubElement(principal, 'UserId').text = sid
    ET.SubElement(principal, 'LogonType').text = 'InteractiveToken'
    ET.SubElement(principal, 'RunLevel').text = 'LeastPrivilege'

    settings = ET.SubElement(task, 'Settings')
    for name, value in (('MultipleInstancesPolicy', 'IgnoreNew'), ('DisallowStartIfOnBatteries', 'false'),
                        ('StopIfGoingOnBatteries', 'false'), ('Hidden', 'true'), ('ExecutionTimeLimit', 'PT0S')):
        ET.SubElement(settings, name).text = value

    action = ET.SubElement(ET.SubElement(task, 'Actions', Context='Operator'), 'Exec')
    ET.SubElement(action, 'Command').text = str(pythonw)
    arguments = ['-X', 'utf8', str(ROOT / 'supervisor.py'), '--run', str(run), '--auto-resume']
    if data_root:
        arguments += ['--recovery-data-root', str(Path(data_root).resolve())]
    ET.SubElement(action, 'Arguments').text = subprocess.list2cmdline(arguments)
    ET.SubElement(action, 'WorkingDirectory').text = str(ROOT)
    return ET.tostring(task, encoding='utf-16', xml_declaration=True)


def register(run):
    """Arm sign-in recovery for this run, propagating failure to the supervisor."""
    if _test_mode():
        return
    run = Path(run).absolute()
    pythonw = PYTHONW.absolute()
    if not pythonw.is_file():
        raise OSError(f'GammaKeeper recovery interpreter is unavailable: {pythonw}')
    definition = _task_xml(run, _user_sid(), pythonw, os.environ.get('GAMMAKEEPER_DATA_ROOT'))
    with tempfile.TemporaryDirectory(prefix='gammakeeper_reboot_') as directory:
        xml = Path(directory) / 'task.xml'
        xml.write_bytes(definition)
        result = _command('schtasks.exe', '/Create', '/TN', task_name(run), '/XML', str(xml), '/F', '/HRESULT')
        if result.returncode:
            raise _failure('Register GammaKeeper recovery task', result)


def remove(run):
    """Disarm this run's task, with a missing task treated as already removed."""
    if _test_mode():
        return
    result = _command('schtasks.exe', '/Delete', '/TN', task_name(run), '/F', '/HRESULT')
    code = result.returncode & 0xffffffff
    if code not in (0, FILE_NOT_FOUND_HRESULT):
        raise _failure('Remove GammaKeeper recovery task', result)
