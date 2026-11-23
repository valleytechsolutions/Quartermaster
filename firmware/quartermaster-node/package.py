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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', default=os.path.join(HERE, 'build'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', '..', 'quartermaster', 'assets', 'node'))
    args = ap.parse_args()

    with open(os.path.join(args.build, 'flasher_args.json')) as f:
        flasher = json.load(f)
    ver = version(args.build)
    table = partitions(os.path.join(args.build, flasher['partition-table']['file']))
    app_offset = int(flasher['app']['offset'], 16)
    if table.get('ota_0') != app_offset:
        raise SystemExit('the app is not at the ota_0 offset')

    os.makedirs(args.out, exist_ok=True)
    merged = os.path.join(args.out, '%s-%s-merged.bin' % (NAME, ver))
    app = os.path.join(args.out, '%s-%s-app.bin' % (NAME, ver))
    shutil.copyfile(os.path.join(args.build, '%s-merged.bin' % NAME), merged)
    shutil.copyfile(os.path.join(args.build, flasher['app']['file']), app)

    index = {
        'version': ver,
        'chip': flasher['extra_esptool_args']['chip'],
        'flash_size': flasher['flash_settings']['flash_size'],
        'flash_mode': flasher['flash_settings']['flash_mode'],
        'flash_freq': flasher['flash_settings']['flash_freq'],
        'merged': entry(merged, 0),
        'app': entry(app, app_offset),
        'bootloader_offset': int(flasher['bootloader']['offset'], 16),
        'partition_table_offset': int(flasher['partition-table']['offset'], 16),
        'partition_offsets': table,
    }
    with open(os.path.join(args.out, '%s.json' % NAME), 'w') as f:
        json.dump(index, f, indent=2, sort_keys=True)
        f.write('\n')
    for old in os.listdir(args.out):
        if old.endswith('.bin') and old not in (os.path.basename(merged), os.path.basename(app)):
            os.remove(os.path.join(args.out, old))


if __name__ == '__main__':
    main()
