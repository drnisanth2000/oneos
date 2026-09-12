"""Lossless extended attributes for private snapshots, without shell parsing."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import stat
import sys


@contextmanager
def metadata_fd(path):
    """Open each parent without following links; pin the final metadata object."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        if sys.platform == 'darwin':
            flags = os.O_RDONLY | os.O_NONBLOCK | 0x00200000  # O_SYMLINK opens the link itself.
        child = os.open(path.name or '/', flags, dir_fd=fd)
        os.close(fd)
        fd = child
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            raise ValueError('unsupported metadata object')
        yield fd
    finally:
        os.close(fd)


def mac_api():
    api = ctypes.CDLL(None, use_errno=True)
    api.flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    api.flistxattr.restype = ctypes.c_ssize_t
    api.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    api.fgetxattr.restype = ctypes.c_ssize_t
    api.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    api.fsetxattr.restype = ctypes.c_int
    api.fremovexattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    api.fremovexattr.restype = ctypes.c_int
    return api


def checked(result):
    if result < 0:
        raise OSError(ctypes.get_errno(), 'extended attribute operation failed')
    return result


def _read(fd):
    if sys.platform != 'darwin':
        return {os.fsencode(name).hex(): os.getxattr(fd, name).hex() for name in os.listxattr(fd)}
    api = mac_api()
    length = checked(api.flistxattr(fd, None, 0, 0))
    names = ctypes.create_string_buffer(length)
    actual = checked(api.flistxattr(fd, names, length, 0))
    if actual != length:
        raise ValueError('extended attributes changed during capture')
    result = {}
    for name in names.raw[:actual].split(b'\x00'):
        if not name:
            continue
        size = checked(api.fgetxattr(fd, name, None, 0, 0, 0))
        value = ctypes.create_string_buffer(size)
        received = checked(api.fgetxattr(fd, name, value, size, 0, 0))
        if received != size:
            raise ValueError('extended attributes changed during capture')
        result[name.hex()] = value.raw[:received].hex()
    return result


def read_xattrs(path):
    with metadata_fd(path) as fd:
        return _read(fd)


def write_xattrs(path, attributes):
    """Set exact captured values on a new staging object; never touch a source."""
    with metadata_fd(path) as fd:
        current = _read(fd)
        api = mac_api() if sys.platform == 'darwin' else None
        for key in current.keys() - attributes.keys():
            name = bytes.fromhex(key)
            if api:
                checked(api.fremovexattr(fd, name, 0))
            else:
                os.removexattr(fd, name)
        for key, encoded in attributes.items():
            if current.get(key) == encoded:
                continue
            name, value = bytes.fromhex(key), bytes.fromhex(encoded)
            if not name or b'\x00' in name:
                raise ValueError('invalid captured extended attribute')
            if api:
                checked(api.fsetxattr(fd, name, value, len(value), 0, 0))
            else:
                os.setxattr(fd, name, value)
        if _read(fd) != attributes:
            raise ValueError('extended attribute copy verification failed')
