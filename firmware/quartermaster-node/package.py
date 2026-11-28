"""Copy a finished build to the place Quartermaster ships it from, with a JSON index for the flasher."""

import argparse
import hashlib
import json
import os
import shutil
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'quartermaster-node'


def partitions(path):
    """Name to offset of every partition in a binary partition table."""
    out = {}
    with open(path, 'rb') as f:
        data = f.read()
    for i in range(0, len(data) - 31, 32):
        magic, _type, _sub, offset, _size = struct.unpack_from('<HBBII', data, i)
        if magic != 0x50AA:
            break
        out[data[i + 12:i + 28].rstrip(b'\0').decode('ascii')] = offset
    return out


def entry(path, offset):
    with open(path, 'rb') as f:
        data = f.read()
    return {
        'file': os.path.basename(path),
        'offset': offset,
        'sha256': hashlib.sha256(data).hexdigest(),
        'size': len(data),
    }


def version(build):
    with open(os.path.join(build, 'project_description.json')) as f:
        return json.load(f)['project_version']


def chip(build, out):
    """Copy one target's merged image and describe it, with the app as a slice of it."""
    with open(os.path.join(build, 'flasher_args.json')) as f:
        flasher = json.load(f)
    ver = version(build)
    target = flasher['extra_esptool_args']['chip']
    table = partitions(os.path.join(build, flasher['partition-table']['file']))
    app_offset = int(flasher['app']['offset'], 16)
    if table.get('ota_0') != app_offset:
        raise SystemExit('%s: the app is not at the ota_0 offset' % target)
    merged = os.path.join(out, '%s-%s-%s.bin' % (NAME, ver, target))
    shutil.copyfile(os.path.join(build, '%s-merged.bin' % NAME), merged)
    with open(merged, 'rb') as f:
        image = f.read()
    with open(os.path.join(build, flasher['app']['file']), 'rb') as f:
        app = f.read()
    if image[app_offset:] != app:
        raise SystemExit('%s: the merged image does not end with the app' % target)
    return ver, target, merged, {
        'flash_size': flasher['flash_settings']['flash_size'],
        'flash_mode': flasher['flash_settings']['flash_mode'],
        'flash_freq': flasher['flash_settings']['flash_freq'],
        'merged': entry(merged, 0),
        'app': {'slice': 'merged', 'offset': app_offset, 'size': len(app),
                'sha256': hashlib.sha256(app).hexdigest()},
        'bootloader_offset': int(flasher['bootloader']['offset'], 16),
        'partition_table_offset': int(flasher['partition-table']['offset'], 16),
        'partition_offsets': table,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--builds', default=HERE, help='the folder with a build-<target> folder for each chip')
    ap.add_argument('--targets', nargs='+', default=['esp32c5', 'esp32c3', 'esp32c6', 'esp32s3'])
    ap.add_argument('--out', default=os.path.join(HERE, '..', '..', 'quartermaster', 'assets', 'node'))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    chips, kept, versions = {}, set(), set()
    for target in args.targets:
        ver, name, merged, info = chip(os.path.join(args.builds, 'build-%s' % target), args.out)
        versions.add(ver)
        chips[name] = info
        kept.add(os.path.basename(merged))
    if len(versions) != 1:
        raise SystemExit('the builds are of different versions: %s' % ', '.join(sorted(versions)))
    index = {'version': versions.pop(), 'chips': chips}
    with open(os.path.join(args.out, '%s.json' % NAME), 'w') as f:
        json.dump(index, f, indent=2, sort_keys=True)
        f.write('\n')
    for old in os.listdir(args.out):
        if old.endswith('.bin') and old not in kept:
            os.remove(os.path.join(args.out, old))


if __name__ == '__main__':
    main()
