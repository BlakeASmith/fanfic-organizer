# Feature showcase

Demos of Fanfic Organizer in Calibre. The plugin is the product; these files are recordings of the real UI, not mockups.

## Fill from AO3

**Selected books → Fill from AO3** identifies works already in the library, then fills missing AO3 metadata (and native EPUBs when that setting is on).

Identify order: AO3 id or URL on the book → URL inside the EPUB → title + author search. Unique hits fill immediately. Several matches open a picker in the same run:

![Fill from AO3 — pick the matching work](fill-from-ao3-picker.jpg)

Here **Home** (title only, author Unknown) matched many AO3 works. The first candidate is selected; **Skip this book** leaves that row unchanged.

After **OK**, Fill writes identifiers, Fandom, tags, and the rest onto those rows. Title-only **Home** became **Coming Home** with the chosen work’s metadata:

![Coming Home after Fill from AO3](fill-from-ao3-filled.jpg)

[Screen recording](fill-from-ao3-identify-and-picker.mp4) (select two books → Fill from AO3 → picker → filled library).
