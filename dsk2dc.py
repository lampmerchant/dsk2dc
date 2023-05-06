#!/usr/bin/env python3

import argparse
import io
import os.path
import struct
import sys
import time


# Dict matching disk byte sizes to disk encoding and format byte values
DISK_TYPES = {
  409600: (0x00, 0x02),
  819200: (0x01, 0x22),
  737280: (0x02, 0x22),
  1474560: (0x03, 0x22),
}


class CRC16:
  '''16-bit CRC calculator.'''
  
  def __init__(self, poly=0x1021, reg=0):
    self.poly = poly
    self.reg = reg
  
  def update(self, data):
    for byte in data:
      self.reg = self.reg ^ byte << 8
      for i in range(8): self.reg = self.reg << 1 & 0xFFFF ^ (self.poly if self.reg & 0x8000 else 0)
    return self
  
  def get_value(self): return self.reg


def dc42_checksum(data):
  '''Yet another stupid checksum algorithm invented by Apple.'''
  c = 0
  for idx in range(0, len(data), 2):
    c += (data[idx] << 8) | data[idx + 1]
    c &= 0xFFFFFFFF
    c = (c >> 1) | (c << 31)
    c &= 0xFFFFFFFF
  return c


def mac_timestamp():
  '''Return the current date/time, adjusted to be relative to the Macintosh epoch.'''
  return int(time.time()) + 2082844800


def pad_bytes(b, pad_length, pad_byte=0):
  '''Pad a byte string to the given length with the given byte, error if string is too long.'''
  if len(b) > pad_length:
    raise ValueError('string "%s" too long; must be no more than %d bytes' % (b.decode('ascii', 'replace'), pad_length))
  return b'%s%s' % (b, bytes(pad_byte for i in range(pad_length - len(b))))


class DiskImage:
  
  def __init__(self, data, name, encoding, format_byte):
    self.data = data
    self.name = name
    self.encoding = encoding
    self.format_byte = format_byte
  
  @classmethod
  def from_file(cls, filename, name=None):
    '''Build a DiskImage from a file, optionally with a custon name.'''
    with open(filename, 'rb') as fp:
      data_size = fp.seek(0, io.SEEK_END)
      if data_size not in DISK_TYPES: raise ValueError('input raw image size %d is not a recognized size' % data_size)
      fp.seek(0)
      data = fp.read()
    encoding, format_byte = DISK_TYPES[data_size]
    if name is None:
      _, name = os.path.split(filename)
      name, _ = os.path.splitext(name)
    if type(name) is not bytes: name = name.encode('ascii', 'replace')
    return cls(data, name, encoding, format_byte)
  
  def dc42_header(self):
    '''Build a Disk Copy 4.2 header for this image.'''
    return struct.pack('>B63sLLLLBBH',
                       len(self.name),            # (uint8) length of disk name
                       pad_bytes(self.name, 63),  # (string) disk name, padded with nulls
                       len(self.data),            # (uint32) data size
                       0,                         # (uint32) tag size
                       dc42_checksum(self.data),  # (uint32) data checksum
                       0,                         # (uint32) tag checksum
                       self.encoding,             # (uint8) disk encoding
                       self.format_byte,          # (uint8) format byte
                       0x0100)                    # (uint16) magic number
  
  def mb_header(self, dc42_header):
    '''Build a MacBinary header for this image.'''
    timestamp = mac_timestamp()
    retval = struct.pack('>BB63s4s4sBBHHHBBLLLLHB14xLHBB',
                         0,                                  # (uint8) old version number
                         len(self.name),                     # (uint8) length of disk name
                         pad_bytes(self.name, 63),           # (string) disk name, padded with nulls
                         b'dImg',                            # (string) file type
                         b'dCpy',                            # (string) file creator
                         0,                                  # (uint8) Finder flags, high byte
                         0,                                  # (uint8) filler
                         0,                                  # (uint16) vertical position
                         0,                                  # (uint16) horizontal position
                         0,                                  # (uing16) window/folder ID
                         0,                                  # (uint8) protected flag
                         0,                                  # (uint8) filler
                         len(self.data) + len(dc42_header),  # (uint32) data fork length
                         0,                                  # (uint32) resource fork length
                         timestamp,                          # (uint32) creation timestamp
                         timestamp,                          # (uint32) last modified timestamp
                         0,                                  # (uint16) length of comment
                         0,                                  # (uint8) Finder flags, low byte
                         0,                                  # (uint32) unpacked file size
                         0,                                  # (uint16) secondary header length
                         129,                                # (uint8) version written
                         129)                                # (uint8) version required to read
    retval += struct.pack('>HH',
                          CRC16().update(retval).get_value(),  # (uint16) CRC of previous 124 bytes
                          0)                                   # (uint16) filler
    return retval
  
  def mb_footer(self, dc42_header):
    '''Build a MacBinary 'footer' (really just the null bytes needed to pad out to a multiple of 128.'''
    return b'\x00' * (127 - (len(self.data) + len(dc42_header) + 127) % 128)
  
  def to_file(self, filename, macbinary=False):
    '''Write this image to a file, a Disk Copy 4.2 image either as a raw data fork or wrapped in MacBinary.'''
    dc42_header = self.dc42_header()
    with open(filename, 'wb') as fp:
      if macbinary: fp.write(self.mb_header(dc42_header))
      fp.write(dc42_header)
      fp.write(self.data)
      if macbinary: fp.write(self.mb_footer(dc42_header))


def main(argv):
  
  parser = argparse.ArgumentParser(description='Convert raw (.dsk) Macintosh disk images to Disk Copy 4.2 (.dc42) images.')
  parser.add_argument('--name', action='store', metavar='NAME', help='disk name in image header')
  parser.add_argument('--output', action='store', metavar='FILENAME', help='override output file name')
  parser.add_argument('--macbinary', action='store_true', help='prepend MacBinary header to output')
  parser.add_argument('filename', help='input raw (.dsk) image file name')
  args = parser.parse_args(argv[1:])
  
  image = DiskImage.from_file(args.filename, args.name)
  
  if args.output is None:
    output_filename = '%s.%s' % (image.name.decode('ascii', 'replace'), 'bin' if args.macbinary else 'dc42')
  else:
    output_filename = args.output
  
  image.to_file(output_filename, args.macbinary)


if __name__ == '__main__': sys.exit(main(sys.argv))
