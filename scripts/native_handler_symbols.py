"""Test-only independent ELF/COFF ownership check for migrated window handlers."""
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent.parent


def defined_symbols(path):
    data = Path(path).read_bytes()
    names = set()
    if data[:4] == b'\x7fELF':
        if data[:7] != b'\x7fELF\x02\x01\x01':
            raise RuntimeError('Expected ELF64 little endian')
        table = struct.unpack_from('<Q', data, 40)[0]
        stride, count = struct.unpack_from('<HH', data, 58)
        if stride != 64 or table + count * stride > len(data):
            raise RuntimeError('Invalid ELF section table')
        sections = [struct.unpack_from('<IIQQQQIIQQ', data, table + i*stride) for i in range(count)]
        for section in sections:
            if section[1] != 2:
                continue
            if section[9] != 24 or section[6] >= count:
                raise RuntimeError('Invalid ELF symbol geometry')
            strings = sections[section[6]]
            strings_data = data[strings[4]:strings[4]+strings[5]]
            for at in range(section[4], section[4]+section[5], 24):
                name, info, _, index, _, _ = struct.unpack_from('<IBBHQQ', data, at)
                if index and info >> 4 in (1, 2):
                    names.add(strings_data[name:strings_data.index(b'\0', name)].decode('utf-8'))
    else:
        machine, _, _, table, count, _, _ = struct.unpack_from('<HHIIIHH', data)
        if machine != 0x8664 or table+count*18+4 > len(data):
            raise RuntimeError('Expected bounded x86_64 COFF symbols')
        strings = data[table+count*18:]
        index = 0
        while index < count:
            at = table+index*18
            raw, _, section, _, storage, aux = struct.unpack_from('<8sIhHBB', data, at)
            if section > 0 and storage == 2:
                if raw[:4] == bytes(4):
                    offset = struct.unpack_from('<I', raw, 4)[0]
                    name = strings[offset:strings.index(b'\0', offset)]
                else:
                    name = raw.rstrip(b'\0')
                names.add(name.decode('utf-8'))
            index += 1+aux
    return names


def verify_handler_ownership(host, kernel):
    source = (ROOT/'bootstrap/selfhost/scalar_handlers.tx').read_text(encoding='utf-8')
    expected = set(re.findall(r'listPush\(names, "([^"]+)"\)', source))
    if len(expected) != 14:
        raise RuntimeError('Handler ownership contract changed')
    object_source = (ROOT/'bootstrap/selfhost/object_handlers.tx').read_text(encoding='utf-8')
    object_handlers = set(re.findall(r'listPush\(names, "([^"]+)"\)', object_source))
    if len(object_handlers) != 10:
        raise RuntimeError('Object handler ownership contract changed')
    expected |= object_handlers
    construct_source = (ROOT/'bootstrap/selfhost/object_construct.tx').read_text(encoding='utf-8')
    constructors = set(re.findall(r'listPush\(names, "([^"]+)"\)', construct_source))
    if len(constructors) != 8:
        raise RuntimeError('Constructor ownership contract changed')
    expected |= constructors
    expected |= {'__tx_native_object_init_v1', '__tx_native_object_payload_v1',
                 '__tx_native_object_clone_view_v1', '__tx_native_object_equal_view_v1',
                 '__tx_native_object_weak_dead_v1'}
    host_names = defined_symbols(host)
    kernel_names = defined_symbols(kernel)
    if expected & host_names or not expected <= kernel_names:
        raise RuntimeError('Handler definitions still belong to host or are missing from kernel')
    if '__tx_native_handler_error_v1' not in host_names:
        raise RuntimeError('Named diagnostic adapter missing')
    print('PASS host owns 0 migrated definitions; kernel owns 31 handlers/accessors/constructors, 5 object helpers and ABI marker', flush=True)
