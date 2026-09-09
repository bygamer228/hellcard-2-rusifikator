#!/usr/bin/env python3
"""Read and write UE .locres v0-v3, preserving source hashes and FText identities.

The writer emits v3 (CityHash64/UTF-16), using only the Python standard library.
locres format reference: akintos/UnrealLocres, commit
b71d735da6aa47ce649bb590eeacf0ce05db1dfd, LocresLib/LocresFile.cs.
No source text is inferred from a translated resource: additions require explicit
namespace, key, source, and translation. Source text must be the EXACT FText source,
including case, whitespace, line endings, and punctuation.

CLI:
  python locres_tools.py inspect source.locres
  python locres_tools.py roundtrip source.locres copy.locres
  python locres_tools.py extend translated.locres additions.json extended.locres
additions.json is a list of {namespace, key, source, translation} objects.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import zlib

MAGIC = bytes.fromhex('0e147475674a03fc4a15909dc3377f1b')
U64 = (1 << 64) - 1
K0 = 0xc3a5c85c97cb3127
K1 = 0xb492b66fbe98f273
K2 = 0x9ae16a3b2f90404f


def source_hash(text: str) -> int:
    """FCrc::StrCrc32 on Windows TCHAR: 4 bytes per UTF-16 code unit, no NUL.

    Not UTF-8 CRC32, nor UTF-16 CRC32. Non-BMP characters are processed as two
    UTF-16 surrogate code units, each zero-extended to a 32-bit little-endian word.
    """
    if '\x00' in text:
        raise ValueError('FText source contains an embedded NUL')
    utf16 = text.encode('utf-16-le')
    widened = b''.join(utf16[i:i + 2] + b'\0\0' for i in range(0, len(utf16), 2))
    return zlib.crc32(widened)


# CityHash64 algorithm adapted from the MIT-licensed implementation at
# akintos/UnrealLocres/LocresLib/CityHash.cs (Google, Inc. and Atvaark).
# Copyright (c) 2011 Google, Inc. Copyright (c) 2014 Atvaark
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
def _rot(value: int, shift: int) -> int:
    value &= U64
    return ((value >> shift) | (value << (64 - shift))) & U64 if shift else value


def _mix(value: int) -> int:
    value &= U64
    return value ^ (value >> 47)


def _hash16(u: int, v: int, mul: int = 0x9ddfea08eb382d69) -> int:
    a = ((u ^ v) * mul) & U64
    a ^= a >> 47
    b = ((v ^ a) * mul) & U64
    b ^= b >> 47
    return (b * mul) & U64


def city_hash64(data: bytes) -> int:
    """The CityHash64 variant used by UE FTextKey v3."""
    n = len(data)
    def f64(i):
        return struct.unpack_from('<Q', data, i)[0]
    def f32(i):
        return struct.unpack_from('<I', data, i)[0]
    def swap(v):
        return int.from_bytes((v & U64).to_bytes(8, 'little'), 'big')
    def weak(i, a, b):
        w, x, y, z = (f64(i + j) for j in (0, 8, 16, 24))
        a = (a + w) & U64
        b = _rot(b + a + z, 21)
        c = a
        a = (a + x + y) & U64
        b = (b + _rot(a, 44)) & U64
        return ((a + z) & U64, (b + c) & U64)
    if n <= 16:
        mul = (K2 + n * 2) & U64
        if n >= 8:
            a, b = (f64(0) + K2) & U64, f64(n - 8)
            return _hash16((_rot(b, 37) * mul + a) & U64,
                           ((_rot(a, 25) + b) * mul) & U64, mul)
        if n >= 4:
            return _hash16(n + (f32(0) << 3), f32(n - 4), mul)
        if n:
            y = data[0] + (data[n >> 1] << 8)
            z = n + (data[n - 1] << 2)
            return (_mix((y * K2) ^ (z * K0)) * K2) & U64
        return K2
    if n <= 32:
        mul = (K2 + n * 2) & U64
        a, b = (f64(0) * K1) & U64, f64(8)
        c, d = (f64(n - 8) * mul) & U64, (f64(n - 16) * K2) & U64
        return _hash16((_rot(a + b, 43) + _rot(c, 30) + d) & U64,
                       (a + _rot(b + K2, 18) + c) & U64, mul)
    if n <= 64:
        mul = (K2 + n * 2) & U64
        a, b, c, d = (f64(0) * K2) & U64, f64(8), f64(n - 24), f64(n - 32)
        e, f, g, h = (f64(16) * K2) & U64, (f64(24) * 9) & U64, f64(n - 8), (f64(n - 16) * mul) & U64
        u = (_rot(a + g, 43) + (_rot(b, 30) + c) * 9) & U64
        v = (((a + g) & U64) ^ d) + f + 1
        v &= U64
        w = (swap((u + v) * mul) + h) & U64
        x = (_rot(e + f, 42) + c) & U64
        y = ((swap((v + w) * mul) + g) * mul) & U64
        z = (e + f + c) & U64
        a = (swap((x + z) * mul + y) + b) & U64
        b = (_mix((z + a) * mul + d + h) * mul) & U64
        return (b + x) & U64
    x = f64(n - 40)
    y = (f64(n - 16) + f64(n - 56)) & U64
    z = _hash16((f64(n - 48) + n) & U64, f64(n - 24))
    v = weak(n - 64, n, z)
    w = weak(n - 32, y + K1, x)
    x = (x * K1 + f64(0)) & U64
    for i in range(0, (n - 1) & ~63, 64):
        x = (_rot(x + y + v[0] + f64(i + 8), 37) * K1) & U64
        y = (_rot(y + v[1] + f64(i + 48), 42) * K1) & U64
        x ^= w[1]
        y = (y + v[0] + f64(i + 40)) & U64
        z = (_rot(z + w[0], 33) * K1) & U64
        v = weak(i, v[1] * K1, x + w[0])
        w = weak(i + 32, z + w[1], y + f64(i + 16))
        z, x = x, z
    return _hash16((_hash16(v[0], w[0]) + _mix(y) * K1 + z) & U64,
                   (_hash16(v[1], w[1]) + x) & U64)


def text_key_hash(text: str) -> int:
    if not text:
        return 0
    value = city_hash64(text.encode('utf-16-le'))
    return ((value & 0xffffffff) + (value >> 32) * 23) & 0xffffffff


@dataclass(frozen=True)
class Entry:
    namespace: str
    key: str
    source_hash: int
    value: str
    namespace_hash: int | None = None
    key_hash: int | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return self.namespace, self.key


@dataclass
class Resource:
    version: int
    entries: list[Entry]
    declared_entries: int | None = None


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError(f'Truncated locres at offset {self.pos}, requested {size}')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def fstring(self):
        length = self.number('<i')
        if length == 0:
            return ''
        raw = self.take(length if length > 0 else -length * 2)
        terminator = b'\0' if length > 0 else b'\0\0'
        if not raw.endswith(terminator):
            raise ValueError('Unterminated FString')
        text = raw[:-len(terminator)].decode('ascii' if length > 0 else 'utf-16-le')
        if '\0' in text:
            raise ValueError('Embedded NUL in FString')
        return text


def read_locres(path: str | Path) -> Resource:
    reader = _Reader(Path(path).read_bytes())
    version = 0
    if reader.data.startswith(MAGIC):
        reader.pos = len(MAGIC)
        version = reader.number('<B')
    if version not in (0, 1, 2, 3):
        raise ValueError(f'Unsupported locres version: {version}')
    strings, refcounts = [], []
    table_offset = None
    if version >= 1:
        table_offset = reader.number('<q')
        if not reader.pos <= table_offset <= len(reader.data) - 4:
            raise ValueError('Invalid string table offset')
        restore = reader.pos
        reader.pos = table_offset
        count = reader.number('<I')
        if count > len(reader.data) // 4:
            raise ValueError('Impossible string table count')
        for _ in range(count):
            strings.append(reader.fstring())
            refcounts.append(reader.number('<i') if version >= 2 else None)
        if reader.pos != len(reader.data):
            raise ValueError('Unexpected bytes after string table')
        reader.pos = restore
    declared = reader.number('<I') if version >= 2 else None
    namespace_count = reader.number('<I')
    entries, indices, seen = [], [], set()
    for _ in range(namespace_count):
        nshash = reader.number('<I') if version >= 2 else None
        namespace = reader.fstring()
        key_count = reader.number('<I')
        for _ in range(key_count):
            keyhash = reader.number('<I') if version >= 2 else None
            key = reader.fstring()
            sourcecrc = reader.number('<I')
            if version >= 1:
                index = reader.number('<i')
                if not 0 <= index < len(strings):
                    raise ValueError('Invalid string table index')
                value = strings[index]
                indices.append(index)
            else:
                value = reader.fstring()
            entry = Entry(namespace, key, sourcecrc, value, nshash, keyhash)
            if entry.identity in seen:
                raise ValueError(f'Duplicate FText identity: {entry.identity}')
            seen.add(entry.identity)
            entries.append(entry)
    if reader.pos != (table_offset if table_offset is not None else len(reader.data)):
        raise ValueError('Unexpected bytes after namespace entries')
    # UE may store a conservative Entries.Num() reserve hint that differs from
    # serialized entries; preserve it as metadata, do not trust it as row count.
    if version >= 2:
        actual = Counter(indices)
        for index, count in enumerate(refcounts):
            if count != actual[index]:
                raise ValueError(f'Invalid string reference count at {index}: {count} != {actual[index]}')
    return Resource(version, entries, declared)


def _fstring(value: str, *, key_string: bool = False) -> bytes:
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('FString must be text without embedded NULs')
    # FString's empty table value has length zero. FTextKey serialization keeps
    # the terminator even for an empty namespace/key (matching the game file).
    if not value and not key_string:
        return struct.pack('<i', 0)
    if value.isascii():
        raw = value.encode('ascii') + b'\0'
        return struct.pack('<i', len(raw)) + raw
    raw = value.encode('utf-16-le') + b'\0\0'
    return struct.pack('<i', -(len(raw) // 2)) + raw


def write_locres(path: str | Path, resource: Resource | list[Entry]) -> None:
    """Emit v3, retaining entry order per namespace and exact source CRCs.

    String table indices and refcounts are rebuilt. Original v3 namespace/key
    hashes must agree with computed values; modified FText identities are refused.
    """
    entries = resource.entries if isinstance(resource, Resource) else resource
    namespaces = OrderedDict()
    texts, text_indices, refs = [], {}, Counter()
    seen = set()
    for entry in entries:
        if entry.identity in seen:
            raise ValueError(f'Duplicate FText identity: {entry.identity}')
        seen.add(entry.identity)
        if not 0 <= entry.source_hash <= 0xffffffff:
            raise ValueError('Invalid source hash')
        if isinstance(resource, Resource) and resource.version == 3:
            for name, hashed in ((entry.namespace, entry.namespace_hash), (entry.key, entry.key_hash)):
                if hashed is not None and text_key_hash(name) != hashed:
                    raise ValueError(f'FText key hash does not match identity: {name!r}')
        namespaces.setdefault(entry.namespace, []).append(entry)
        if entry.value not in text_indices:
            text_indices[entry.value] = len(texts)
            texts.append(entry.value)
        refs[entry.value] += 1
    body = bytearray(struct.pack('<II', len(entries), len(namespaces)))
    for namespace, members in namespaces.items():
        body.extend(struct.pack('<I', text_key_hash(namespace)))
        body.extend(_fstring(namespace, key_string=True))
        body.extend(struct.pack('<I', len(members)))
        for entry in members:
            body.extend(struct.pack('<I', text_key_hash(entry.key)))
            body.extend(_fstring(entry.key, key_string=True))
            body.extend(struct.pack('<Ii', entry.source_hash, text_indices[entry.value]))
    table = bytearray(struct.pack('<I', len(texts)))
    for value in texts:
        table.extend(_fstring(value))
        table.extend(struct.pack('<i', refs[value]))
    result = MAGIC + b'\x03' + struct.pack('<q', 25 + len(body)) + body + table
    Path(path).write_bytes(result)


def add_entries(resource: Resource, additions: list[dict]) -> Resource:
    """Add new entries strictly; refuse a duplicate identity, including exact ones."""
    entries, seen = list(resource.entries), {e.identity for e in resource.entries}
    for row in additions:
        required = ('namespace', 'key', 'source', 'translation')
        if any(not isinstance(row.get(k), str) for k in required):
            raise ValueError(f'Addition requires string fields {required}')
        identity = (row['namespace'], row['key'])
        if not row['key'] or not row['source'] or not row['translation']:
            raise ValueError('New key, source, and translation must be nonempty')
        if identity in seen:
            raise ValueError(f'Duplicate/new entry already exists: {identity}')
        seen.add(identity)
        entries.append(Entry(*identity, source_hash(row['source']), row['translation']))
    return Resource(resource.version, entries, len(entries))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('input')
    roundtrip = sub.add_parser('roundtrip')
    roundtrip.add_argument('input')
    roundtrip.add_argument('output')
    extend = sub.add_parser('extend')
    extend.add_argument('input')
    extend.add_argument('additions')
    extend.add_argument('output')
    args = parser.parse_args()
    resource = read_locres(args.input)
    old_count = len(resource.entries)
    if args.command == 'extend':
        resource = add_entries(resource, json.loads(Path(args.additions).read_text(encoding='utf-8-sig')))
    if args.command != 'inspect':
        write_locres(args.output, resource)
        reloaded = read_locres(args.output)
        semantic = lambda r: [(e.namespace, e.key, e.source_hash, e.value) for e in r.entries]
        if semantic(resource) != semantic(reloaded):
            raise ValueError('Output semantic roundtrip mismatch')
    print(json.dumps({'version': resource.version, 'entries': len(resource.entries),
                      'declared_entries': resource.declared_entries,
                      'namespaces': len({e.namespace for e in resource.entries}),
                      'added': len(resource.entries) - old_count}, ensure_ascii=False))


if __name__ == '__main__':
    main()
