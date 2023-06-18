# KRDS
KRDS - A parser for Kindle reader data store files

Kindle ereaders store information related to annotations, bookmarks,
highlights, etc. in files in an undocumented data format. it encodes
he name of each object being serialized along with a list of property
values. Values each have an associated data type, such as integer or
string.

John Howell has written a Python script to parse these files. The main
function accepts an input file name, parses it into a Python data
structure, and outputs the result as a human readable JSON file.

The original script was published on the mobileread thread
https://www.mobileread.com/forums/showthread.php?t=322172
which has more information regarding its development and use.

