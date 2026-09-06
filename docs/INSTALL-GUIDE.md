# Installing a floppy onto a hard drive

Copying a game disk into an HDF gives you the files. It does not give you
something that runs. The title still expects to be booted from `DF0:`, and the
hard drive still has no idea it is there. This guide covers the three ways
Amiga File Forge closes that gap, and it is honest about what each one can and
cannot do.

The choice appears in the import dialog whenever the pane you are dropping onto
is a mounted AmigaDOS volume on a hard drive. Under **Import as** you get:

* Copy the disc contents in as they are, which is the behaviour that has always
  been there.
* Install it onto this drive, which is what this guide is about.
* Store the original image as an ordinary file, when the disc image itself is
  what you want to keep.

A floppy pane offers no install option, because a floppy has nowhere to install
to. A drive showing its partition table offers none either: a partition table is
not a volume. Enter a partition first.

## Method 1: stage it for installing later

This is the default, and for a multi-disc set it is usually the right answer.

Each disc is extracted into a staging drawer named after the title. Stage the
second disc under the same title and its files are merged into the same tree,
which is what an installer expects to be pointed at and what you would copy to a
real machine. Nothing is emulated, nothing is downloaded, and nothing is guessed
at, so this mode always works and always works quickly.

Where two discs carry the same path with genuinely different contents, the first
is kept and the later one is filed under `alternates/` beside the payload. A set
is never silently reduced to whichever disc you staged last. The staging summary
lists every conflict so you can see what happened.

Protection bits and comments are written beside each file in the same `.inf`
sidecar Amiga File Forge already uses for exports, so a staged tree brought back
in later still has them. A loader that lost its `e` bit will not start, and the
failure looks nothing like a missing permission, so this matters more than it
sounds.

Staging a disc under a label that is already there replaces it, files and all.
Re-staging Disk 1 after correcting it leaves you with one Disk 1 holding the
corrected files, and any conflict previously recorded against that disc is
dropped. A set that grew every time it was fixed, or that filed your correction
away as an alternative to the broken file, would be impossible to reason about
by the time you came to install it.

Staged titles are kept under the working directory. Point
`AMIGA_INSTALL_STAGING_DIR` somewhere else when you want them written straight
to a share or a card the Amiga can reach.

Come back to them with **Tools -> Staged installations**. That lists every
title waiting, what discs it holds and where the files are, and installs one
into the volume the pane has open. It also names any file that differed between
discs, so a set that needed a judgement call says so rather than looking
complete. Discarding a title deletes only the extracted copies; the original
images are untouched.

## Method 2: install with WHDLoad

WHDLoad is how most Amiga games and demos are made to run from a hard drive. It
comes in two halves, and only one of them can be fetched for you.

**The program** is published by its author at `whdload.de`. Amiga File Forge
checks whether the drive already has `C:WHDLoad`, reads its version from the
program's own `$VER:` string, and installs the current release if there is none.
Aminet is used as a fallback if the author's site cannot be reached. The
`C:` tools and the `S:` startup and cleanup scripts are copied directly rather
than by running the archive's `Install` script under emulation: the destinations
are fixed and known, so booting a machine to rediscover them would cost a minute
per image and add a way to fail that copying does not have.

An existing `S:WHDLoad.prefs` is never overwritten. If you have tuned where
WHDLoad writes its debug output on that machine, reinstalling leaves your file
alone.

**A slave** is the small per-title patch that teaches WHDLoad one game, and it
cannot be downloaded. The author's site refuses its `/games/` index to anything
that is not a browser session, and Aminet does not carry slaves. Amiga File
Forge does not pretend otherwise and offers no button that would always fail. A
slave reaches an image because it is already there, or because you supply one.
You can hand it either the bare `.slave` file or the small LHA it was published
in, and the archive is unpacked on the way through, so you do not need an LHA
tool of your own.

An install without a slave is reported as incomplete rather than presented as
finished. The title's drawer is created and its files are staged into it, ready
for the slave whenever you have it.

## Method 3: run the disc's own installer

Some software cannot be second-guessed at all. Productivity titles ask which
drawer, which language, which screen mode, and no tool has an answer to those
on your behalf.

This mode stops trying. It boots the drive in the emulator with the disc already
in `DF0:` and hands you the keyboard. The drive is attached whole, the same way
a hard-drive launch already works, so the installer sees the partitions and the
Workbench you actually built. Up to four discs can be inserted at once, filling
`DF0:` to `DF3:`, so a disc swap is a menu choice rather than a restart.

Because the emulator boots the drive rather than the disc, the drive needs a
working Workbench on it before this is useful. It also needs a Kickstart ROM for
the machine in your hardware profile; see the [firmware notes](../firmware/README.md).

Whichever mode you choose, the disc is staged first. An install that fails
halfway has still preserved the disc's contents somewhere you can finish by hand.

## What gets an undo point

Installing a staged title, installing WHDLoad and placing a slave all change a
volume, and each takes an undo checkpoint before it runs. Staging changes no
image at all, so it takes none. Booting the emulator changes nothing Amiga File
Forge owns.

## Reading LHA archives

Amiga File Forge decodes LHA itself rather than calling out to `lha` or
`lhasa`. That keeps the container, the Debian package and the Snap behaving
identically instead of leaving one of them with a missing tool nobody notices
until a user hits it. Header levels 0, 1 and 2 are read, and the `-lh0-`,
`-lh4-`, `-lh5-`, `-lh6-` and `-lh7-` methods are decompressed, which is
everything the Amiga world produced. Every member is checked against the CRC the
archive stores for it, so a damaged download is reported rather than written
into a disk image.

A method this build cannot expand still lists correctly and names itself when
you try to read it, because "this archive uses `-lh1-`" is a fact you can act
on and "something went wrong" is not.
