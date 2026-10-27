"""Portable nonblocking process locks for account and first-run state."""

import os


# ==========================================
# Function: Acquire the same persistent lock file on Linux and native Windows.
# Method: Use POSIX flock or a nonblocking one-byte CRT range lock with a stable file offset.
# ==========================================
def acquire(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {13, 11, 36}:
                raise BlockingIOError('File is locked by another process') from error
            if error.errno == 22:
                raise RuntimeError('当前文件系统不支持 Windows 文件锁，请将完整程序包解压到 Windows 本地磁盘') from error
            raise
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


# ==========================================
# Function: Release only the byte range or advisory lock owned by this handle.
# Method: Restore offset zero before the Windows CRT unlock and retain POSIX flock semantics.
# ==========================================
def release(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
