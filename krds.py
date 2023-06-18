#!/usr/bin/python3

from __future__ import (unicode_literals, division, absolute_import, print_function)

import argparse
import collections
import datetime
import io
import json
import logging
import os
import struct
import sys

__license__ = "GPL v3"
__copyright__ = "2019, John Howell <jhowell@acm.org>"

# 1.1 - 2023-06-18 - tomsem additions for kindle Scribe

# For background and change history, see:
# https://www.mobileread.com/forums/showthread.php?t=322172
# https://github.com/K-R-D-S/KRDS

def main():
    parser = argparse.ArgumentParser(prog="python krds.py", description="Convert Kindle reader data store files to JSON")
    parser.add_argument("pathname", help="Pathname to be processed (.azw3f, .azw3r, .mbp1, .mbs, .yjf, .yjr)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    if not os.path.isfile(args.pathname):
        raise Exception("File does not exist: %s" % args.pathname)

    print('Decoding %s' % args.pathname)

    with io.open(args.pathname, "rb") as infile:
        binary_data = infile.read()

    decoded_objects = KindleReaderDataStore(logging, binary_data).deserialize()

    out_pathname = args.pathname + ".json"

    with io.open(out_pathname, "wb" if sys.version_info[0] == 2 else "w") as outfile:
        json.dump(decoded_objects, outfile, indent=4, separators=(",", ": "))

    print("Completed conversion to %s" % out_pathname)


class KindleReaderDataStore(object):
    SIGNATURE = b"\x00\x00\x00\x00\x00\x1A\xB1\x26"

    def __init__(self, log, data=None):
        self.krds = None
        self.log = log
        self.data = data    # for deserialize

    def deserialize(self):
        self.krds = Deserializer(self.data)

        signature = self.krds.extract(len(self.SIGNATURE))
        if signature != self.SIGNATURE:
            raise Exception("KindleReaderDataStore signature is incorrect")

        first_value = self.decode_next()
        if first_value != 1:
            raise Exception("first_value = %s" % repr(first_value))

        value_cnt = self.decode_next()
        value = collections.OrderedDict()

        for _ in range(value_cnt):
            try:
                val = self.decode_next()
            except Exception:
                self.log.info("KindleReaderDataStore decode failed at offset %d" % (len(self.data) - len(self.krds)))
                self.log.info("partial value = %s" % repr(value))
                raise

            for k, v in val.items():
                if k in value:
                    raise Exception("KindleReaderDataStore has duplicate item %s" % k)

                value[k] = v

        if len(self.krds) != 0:
            self.log.error("KindleReaderDataStore has %d bytes of extra data" % len(self.krds))

        return value

    DATATYPE_BOOLEAN = 0            # 0=false, 1=true
    DATATYPE_INT = 1                # 4-byte signed integer
    DATATYPE_LONG = 2               # 8-byte signed integer
    DATATYPE_UTF = 3                # boolean + 2-byte length + UTF-8 data (empty string if bool is True)
    DATATYPE_DOUBLE = 4             # 8-byte float
    DATATYPE_SHORT = 5              # 2 byte signed integer
    DATATYPE_FLOAT = 6              # 4-byte float
    DATATYPE_BYTE = 7               # signed byte
    DATATYPE_CHAR = 9               # single character
    DATATYPE_OBJECT_BEGIN = -2      # named object data structure (name [utf] + data)
    DATATYPE_OBJECT_END = -1        # end of data for object

    def decode_next(self, datatype=None):
        if datatype is None:
            datatype = self.krds.unpack("b")

        if datatype == self.DATATYPE_BOOLEAN:
            b = self.krds.unpack("b")
            if b == 0:
                value = False
            elif b == 1:
                value = True
            else:
                raise Exception("Unknown boolean value %d" % b)

        elif datatype == self.DATATYPE_INT:
            value = self.krds.unpack(">l")

        elif datatype == self.DATATYPE_LONG:
            value = self.krds.unpack(">q")

        elif datatype == self.DATATYPE_UTF:
            if self.decode_next(self.DATATYPE_BOOLEAN):
                value = ""
            else:
                value = self.krds.extract(self.krds.unpack(">H")).decode("utf-8")

        elif datatype == self.DATATYPE_DOUBLE:
            value = self.krds.unpack(">d")

        elif datatype == self.DATATYPE_SHORT:
            value = self.krds.unpack(">h")

        elif datatype == self.DATATYPE_FLOAT:
            value = self.krds.unpack(">f")

        elif datatype == self.DATATYPE_BYTE:
            value = self.krds.unpack("b")

        elif datatype == self.DATATYPE_CHAR:
            value = self.krds.unpack("c").decode("utf-8")

        elif datatype == self.DATATYPE_OBJECT_BEGIN:
            name = self.decode_next(self.DATATYPE_UTF)
            val = []

            while self.krds.unpack("b", advance=False) != self.DATATYPE_OBJECT_END:
                val.append(self.decode_next())

            self.krds.unpack("b")  # self.DATATYPE_OBJECT_END
            return self.decode_object(name, val)

        else:
            raise Exception("Unknown datatype %d" % datatype)

        return value

    ANNOT_CLASS_NAMES = {
        0: "annotation.personal.bookmark",
        1: "annotation.personal.highlight",
        2: "annotation.personal.note",
        3: "annotation.personal.clip_article",  # value not verified
        10: "annotation.personal.handwritten_note",
        11: "annotation.personal.sticky_note",
    }

    def decode_object(self, name, val):
        obj = collections.OrderedDict()

        if name in {
                "clock.data.store", "dictionary", "lpu", "pdf.contrast", "sync_lpr", "tpz.line.spacing",
                "XRAY_OTA_UPDATE_STATE", "XRAY_SHOWING_SPOILERS", "XRAY_SORTING_STATE", "XRAY_TAB_STATE"}:
            obj = val.pop(0)    # single value

        elif name in {"dict.prefs.v2", "EndActions", "ReaderMetrics", "StartActions", "Translator", "Wikipedia"}:
            for _ in range(val.pop(0)):     # key/value pairs
                k = val.pop(0)
                obj[k] = val.pop(0)

        elif name in {"buy.asin.response.data", "next.in.series.info.data", "price.info.data"}:
            obj = val.pop(0)    # single value json

        elif name == "erl":
            obj = self.decode_position(val.pop(0))

        elif name in {"lpr"}:
            version = val.pop(0)
            if isinstance(version, str):
                obj["position"] = self.decode_position(version)   # old-style lpr
            elif version <= 2:
                obj["position"] = self.decode_position(val.pop(0))
                time = val.pop(0)
                obj["time"] = datetime.datetime.fromtimestamp(time / 1000.0).isoformat() if time != -1 else None
            else:
                raise Exception("Unknown lpr version %d" % version)

        elif name in {"fpr", "updated_lpr"}:
            obj["position"] = self.decode_position(val.pop(0))
            time = val.pop(0)
            obj["time"] = datetime.datetime.fromtimestamp(time / 1000.0).isoformat() if time != -1 else None
            timezone = val.pop(0)
            obj["timeZoneOffset"] = timezone if timezone != -1 else None
            obj["country"] = val.pop(0)
            obj["device"] = val.pop(0)

        elif name == "annotation.cache.object":
            for _ in range(val.pop(0)):
                annotation_type = val.pop(0)
                annot_class_name = self.ANNOT_CLASS_NAMES.get(annotation_type)
                if annot_class_name is None:
                    raise Exception("Unknown annotation type %d" % annotation_type)

                annotations = []
                for annotation in val.pop(0)["saved.avl.interval.tree"]:
                    if len(annotation) != 1 or annot_class_name not in annotation:
                        raise Exception("Unknown annotation format: %s" % repr(annotation))

                    annotations.append(annotation[annot_class_name])

                obj[annot_class_name] = annotations

        elif name == "saved.avl.interval.tree":
            obj = [val.pop(0) for _ in range(val.pop(0))]   # annotation.personal.xxx

        elif name in self.ANNOT_CLASS_NAMES.values():
            obj["startPosition"] = self.decode_position(val.pop(0))
            obj["endPosition"] = self.decode_position(val.pop(0))
            obj["creationTime"] = datetime.datetime.fromtimestamp(val.pop(0) / 1000.0).isoformat()
            obj["lastModificationTime"] = datetime.datetime.fromtimestamp(val.pop(0) / 1000.0).isoformat()
            obj["template"] = val.pop(0)

            if name == "annotation.personal.note":
                obj["note"] = val.pop(0)
            elif name == "annotation.personal.handwritten_note":
                obj["handwritten_note_nbk_ref"] = val.pop(0)
            elif name == "annotation.personal.sticky_note":
                obj["sticky_note_nbk_ref"] = val.pop(0)

        elif name == "apnx.key":
            obj["asin"] = val.pop(0)        # typically ISBN of print edition source of page numbers
            obj["cdeType"] = val.pop(0)
            obj["sidecarAvailable"] = val.pop(0)
            obj["oPNToPosition"] = [val.pop(0) for _ in range(val.pop(0))]
            obj["first"] = val.pop(0)       # int
            obj["unknown1"] = val.pop(0)    # int
            obj["unknown2"] = val.pop(0)    # int
            obj["pageMap"] = val.pop(0)     # "pageMapString"

        elif name == "fixed.layout.data":
            obj["unknown1"] = val.pop(0)    # boolean
            obj["unknown2"] = val.pop(0)    # boolean
            obj["unknown3"] = val.pop(0)    # boolean

        elif name == "sharing.limits":
            obj["accumulated"] = val.pop(0)

        elif name == "language.store":
            obj["language"] = val.pop(0)    # string
            obj["unknown1"] = val.pop(0)    # int

        elif name == "periodicals.view.state":
            obj["unknown1"] = val.pop(0)    # string
            obj["unknown2"] = val.pop(0)    # string

        elif name == "font.prefs":
            obj["typeface"] = val.pop(0)        # string with multiple tokens (default "_INVALID_")
            obj["lineSp"] = val.pop(0)          # int (default -1)
            obj["size"] = val.pop(0)            # int (default -1)
            obj["align"] = val.pop(0)           # int (default -1)
            obj["insetTop"] = val.pop(0)        # int (default -1)
            obj["insetLeft"] = val.pop(0)       # int (default -1)
            obj["insetBottom"] = val.pop(0)     # int (default -1)
            obj["insetRight"] = val.pop(0)      # int (default -1)
            obj["unknown1"] = val.pop(0)        # int (default -1)

            if len(val):
                obj["bold"] = val.pop(0)        # int (default -1)

            if len(val):
                obj["userSideloadableFont"] = val.pop(0)    # string

            if len(val):
                obj["customFontIndex"] = val.pop(0)         # int (default -1)

            if len(val):
                obj["mobi7SystemFont"] = val.pop(0)         # string (font to use in place of user font for mobi7 books)

            if len(val):
                obj["mobi7RestoreFont"] = val.pop(0)        # boolean (restore user font when exiting mobi7 book)

            if len(val):
                obj["readingPresetSelected"] = val.pop(0)   # string

            if len(val):
                obj["unknown2"] = val.pop(0)

        elif name == "purchase.state.data":
            obj["state"] = val.pop(0)           # string
            obj["time"] = datetime.datetime.fromtimestamp(val.pop(0) / 1000.0).isoformat()

        elif name == "timer.data.store":
            obj["on"] = val.pop(0)              # boolean
            obj["readingTimerModel"] = val.pop(0)
            obj["version"] = val.pop(0)         # long

        elif name == "timer.data.store.v2":
            obj["on"] = val.pop(0)              # boolean
            obj["readingTimerModel"] = val.pop(0)
            obj["version"] = val.pop(0)         # long
            obj["lastOption"] = val.pop(0)      # int

        elif name == "timer.model":
            obj["version"] = val.pop(0)
            obj["totalTime"] = val.pop(0)
            obj["totalWords"] = val.pop(0)
            obj["totalPercent"] = val.pop(0)
            obj["averageCalculator"] = val.pop(0)["timer.average.calculator"]

        elif name == "timer.average.calculator":
            obj["samples1"] = [val.pop(0) for _ in range(val.pop(0))]   # doubles
            obj["samples2"] = [val.pop(0) for _ in range(val.pop(0))]   # doubles
            obj["normalDistributions"] = [val.pop(0)["timer.average.calculator.distribution.normal"] for _ in range(val.pop(0))]
            obj["outliers"] = [val.pop(0)["timer.average.calculator.outliers"] for _ in range(val.pop(0))]

        elif name == "timer.average.calculator.distribution.normal":
            obj["count"] = val.pop(0)           # long
            obj["sum"] = val.pop(0)             # double
            obj["sumOfSquares"] = val.pop(0)    # double

        elif name == "timer.average.calculator.outliers":
            obj = [val.pop(0) for _ in range(val.pop(0))]   # doubles

        elif name == "book.info.store":
            obj["numberOfWords"] = val.pop(0)           # long, Num words known in book
            obj["percentOfBook"] = val.pop(0)           # double, Percentage of book for the known words

        elif name == "page.history.store":
            obj = [val.pop(0)["page.history.record"] for _ in range(val.pop(0))]

        elif name == "page.history.record":
            obj["position"] = self.decode_position(val.pop(0))
            obj["time"] = datetime.datetime.fromtimestamp(val.pop(0) / 1000.0).isoformat()

        elif name == "reader.state.preferences":
            obj["fontPreferences"] = val.pop(0)
            obj["leftMargin"] = val.pop(0)      # int
            obj["rightMargin"] = val.pop(0)     # int
            obj["topMargin"] = val.pop(0)       # int
            obj["bottomMargin"] = val.pop(0)    # int
            obj["unknown1"] = val.pop(0)        # boolean

        else:
            self.log.error("Unknown data structure %s" % name)
            obj = val
            val = []

        if len(val):
            raise Exception("Excess values found for structure %s: %s" % (name, repr(val)))

        return {name: obj}

    @staticmethod
    def decode_position(position):
        return position


class Deserializer(object):
    def __init__(self, data):
        self.buffer = data
        self.offset = 0

    def unpack(self, fmt, advance=True):
        result = struct.unpack_from(fmt, self.buffer, self.offset)[0]

        if advance:
            self.offset += struct.calcsize(fmt)

        return result

    def extract(self, size=None, upto=None, advance=True):
        if size is None:
            size = len(self) if upto is None else (upto - self.offset)

        data = self.buffer[self.offset:self.offset + size]

        if len(data) < size or size < 0:
            raise Exception("Deserializer: Insufficient data (need %d bytes, have %d bytes)" % (size, len(data)))

        if advance:
            self.offset += size

        return data

    def __len__(self):
        return len(self.buffer) - self.offset


if __name__ == "__main__":
    main()
