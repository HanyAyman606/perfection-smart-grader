from PySide6.QtCore import QStorageInfo

def test_print_drives():
    for v in QStorageInfo.mountedVolumes():
        print(v.rootPath(), "valid:", v.isValid(), "ready:", v.isReady())
    assert True