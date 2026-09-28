# Preparing a hard drive and installing floppies onto it

Copying a game disk into an HDF gives you the files. It does not give you
something that runs. The title still expects to be booted from `DF0:`, and the
hard drive still has no idea it is there. This guide covers the ways Amiga File
Forge closes that gap, and it is honest about what each one can and cannot do.

It starts with the drive itself. A blank partition is not a machine you can
use, so the first section covers installing AmigaOS onto it from your own
Workbench floppies. The rest covers getting a title onto the drive once there
is a system there to run it.

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

## Preparing the drive: install Workbench

Choose **Tools -> Install Workbench** with a partition open, then point at the
folder holding your Workbench floppy images, or pick the images yourself.

No AmigaOS is shipped with Amiga File Forge and none is downloaded. It is not
free to redistribute, so the disks have to be the ones you own. ADF, ADZ, DMS
and HFE images are all read, including the zipped-per-disk form a TOSEC
collection uses.

Pointing at a whole collection folder is fine. A TOSEC `Workbench` folder holds
every release together, and everything in it that is not part of one is listed
as ignored. The disks you did not choose are simply not installed.

**Disks are recognised by the volume name inside each image, not by its file
name.** ADF collections are named inconsistently - `wb31_workbench.adf`,
`Workbench 3.1 (Disk 2 of 6).adf`, `disk02.adf` - and a renamed file says
nothing at all about its contents, while the volume name was written by
Commodore and travels with the data. That is what lets you point at a folder
rather than assembling the set by hand; anything in it that is not part of a
release is listed as ignored rather than silently included.

**The release is decided once, from the Workbench disk, and every other disk is
matched to it.** Mixing releases is the classic way to end up with a drive that
looks complete and boots to a Guru: a 2.0 Extras drawer on a 3.1 system, or a
Locale disk from a release that had none, produces a system whose parts disagree
about what the others provide. A disk from another release is left out rather
than mixed in. Where a collection holds several dumps of the same disk, one that
says it was verified is preferred and one that says it was modified or cracked
is avoided.

Each disk lands where the AmigaOS install script would put it:

| Disk | Lands in | Notes |
| --- | --- | --- |
| Workbench | volume root | Required. The system itself: `C`, `L`, `Libs`, `Devs`, `S` and the desktop. |
| Extras | volume root | Merged in after Workbench. |
| Fonts | `Fonts` | |
| Locale | `Locale` | AmigaOS 3.x only. |
| Storage | `Storage` | Drivers and monitors held back until they are wanted. |
| Classes | `Classes` | BOOPSI classes and datatypes. |
| GlowIcons | volume root | The AmigaOS 3.5 icon set. |
| Backdrops | `Backdrops` | |
| Install | `Install` | Kept in its own drawer on purpose. |

**The order is fixed and it matters.** Workbench is copied before Extras so that
its full `C:`, `L:` and `Libs:` are not overwritten by the cut-down copies the
other disks carry, and the Install disk is kept in its own drawer for the same
reason. Only the Workbench disk is required; anything else you do not have is
simply left out. If the automatic choice is wrong, change which disc plays each
part before installing.

`T`, `Trashcan`, `Devs/DOSDrivers` and `Prefs/Env-Archive` are created
afterwards, because the install script makes them and no disk provides them. A
system with no `T:` cannot write a temporary file.

**Files already on the volume are left alone.** The volume is written into
rather than formatted, so a drive you have already partitioned, named and put
work on is added to rather than replaced, and installing twice does not undo
hand edits made in between.

A hard drive boots from the flag in its Rigid Disk Block rather than from a
floppy boot block, so if the partition is not marked bootable the install says
so rather than leaving you with a drive that silently will not start.

## AmigaOS 3.5 and 3.9, which came on CD

These two releases were published on CD, and Amiga File Forge installs them
itself. When the installation finishes the drive holds the system and starts
the machine. There is nothing left to do inside an emulator.

Choose **Tools -> Install AmigaOS 3.5 or 3.9** with a partition open, then
point it at the ISO of the disc you own. Nothing is downloaded, and no AmigaOS
is shipped.

### How the installation is made

A CD carries the system already laid out as directory trees, so an
installation is a copy made in the right order. Every source and destination
was read out of the Installer script each disc carries, `OS3.5Install` and
`OS3.9Install`, and is the same layout the PiStorm imager writes.

| Release | Layers, in the order they are laid down |
| --- | --- |
| AmigaOS 3.9 | `Workbench3.5`, a complete system, then `Workbench3.9` over it, then Locale, keymaps, printer drivers, the updated commands, FastFileSystem, the extra libraries and the backdrops |
| AmigaOS 3.5 | Workbench 3.1 and Extras 3.1 from the disc's own `OS-Version3.1` drawer, then the 3.5 `Workbench` over them, then the same additions |

Several destinations are not the obvious ones. `Extras/Backdrops` goes to
`Prefs/Presets/Backdrops`, and the keymaps and printer drivers are copied out
of `Storage` into `Devs` while staying in `Storage` as well.

The layers are resolved by name before anything is written, so each file is
written once, from the newest layer that supplies it. Protection bits and file
comments recorded on the disc are kept.

Every language, keymap and printer driver on the disc is installed. The disc's
own installer asks which to leave out. Leaving nothing out costs a few
megabytes and means nothing has to be fetched from the disc later.

### What is checked first

- **The disc.** It is identified by the volume name Commodore wrote,
  `AmigaOS3.5` or `AmigaOS3.9`, and confirmed by its system trees. A disc whose
  name has been changed is recognised by those trees. A disc that lacks a tree
  the release cannot do without is refused, and the tree is named.
- **The processor.** Both releases need a 68020 or better. An A1200, A3000,
  A4000 or CD32 qualifies on its own, and so does any machine carrying a
  68020, 68030, 68040 or 68060 accelerator, or a PiStorm. A stock A500 or A600
  cannot run either release, and is told so before anything is written.
- **The drive.** A partition has to be open, and it has to have room. It does
  not need a system on it.

The dialog then lists what will be installed, layer by layer, with where each
lands and how many files it supplies.

### Installing over a system that is already there

A file on the drive is replaced when the release carries one of the same name,
which is what makes installing over Workbench 3.1 an upgrade. Everything the
release does not carry is left exactly as it was, so software already on the
drive stays. `S/User-Startup` is never replaced.

An undo point is taken first, and **Edit -> Undo last change** puts the drive
back as it was.

### Update packs

A CD installation is not a finished system. Both releases had update packs
published after them, the BoingBags. In the same dialog, choose **Choose
BoingBag archives** and pick the LHA archives you have. One archive may hold
several packs, and the packs are found inside it at whatever depth. Each pack
found is listed with a tick box, and they are applied oldest first.

| Pack | How it is applied |
| --- | --- |
| BoingBag 1 and 2 for AmigaOS 3.5 | Copied over the system |
| BoingBag 1 and 2 for AmigaOS 3.9 | Plain files are copied. The system fixes are applied by the pack's own Updater |
| BoingBags 3 and 4 for AmigaOS 3.9 | Copied over the system, with the build of each library chosen for the processor in the hardware profile |

BoingBags 1 and 2 for 3.9 keep their fixes in `AmigaOS-Update`, an archive in
which every file is encrypted and which only Haage & Partner's `Updater` can
open. Amiga File Forge does not open it. It runs the pack's own `Updater` in
FS-UAE, on a copy of the system being installed, with the release disc
attached because the Updater looks for it. The emulator opens a window of its
own for a few minutes for each pack and closes it again. Nothing needs doing
in that window. What the Updater produced is then written to the drive with
the rest of the system.

That needs FS-UAE and a Kickstart 3.1 ROM for the machine in the hardware
profile. Where either is missing the dialog says so beforehand, the plain
files of those packs are still installed, and every fix that could not be
applied is listed by name afterwards.

BoingBags 3 and 4 are a community release. They replace core components and
expect BoingBags 1 and 2 underneath. Untick them for a stock system.

## Running the disc's own installer instead

The same dialog offers **Run the disc's installer instead**, for somebody who
wants to choose what the installer leaves out or to use something else on the
disc. This is not needed to install the release. It starts the machine in the
emulator with the disc in its CD drive and hands over the keyboard.

Something has to start the machine. A drive with a system on it starts itself,
and an empty drive is started from the system the disc carries, described
below. With an empty drive and a disc that carries no such system, the button
is not available.

### Starting from an empty drive

A newly partitioned drive has nothing on it to start a machine from. Both
discs carry a complete system in a drawer called `Emergency-Boot`, which is
what the installer means when it says to "boot from your Emergency-Disk to
make the update or full installation".

The machine is started from that system. It is copied onto a small drive of
its own and attached beside the drive being installed onto, with a higher boot
priority, because an emulated A1200 does not boot from its CD drive. The lines
of its startup that expect the emergency floppy in `DF0:` are commented out,
since each would stop the boot with a requester asking for the disk. In the
installer, choose the full installation and give it the empty partition.

Nothing from the emergency system is put on your drive. The installer decides
what goes there, and the small drive is discarded when the emulator closes.

### What the emulator is given

The emulator is given the drive in the pane itself, not a copy, so what the
installer writes is on the drive when the emulator closes. An undo point is
taken before the machine starts, and **Edit -> Undo last change** afterwards
puts the drive back as it was. While the emulator is running the drive cannot
be changed from the workbench, because two programs writing to one drive
corrupt it.

FS-UAE installed as a snap cannot read the hidden folder the workbench keeps
its images in. The drive and the disc are given to it under a second name in
`~/snap/fsuae/common/amiga-file-forge`, which it can read. That is a hard link
and not a copy, so a drive of a hundred gigabytes is handed over at once. Where
a hard link cannot be made, because the two folders are on different disks, the
drive is copied without its empty space and copied back afterwards.

### The disc is visible without a driver

The emulator mounts the disc as `CD0:` itself, before AmigaDOS starts, so the
machine sees it whether or not the drive has a CD driver of its own. Nothing is
written to the drive to prepare it. Earlier releases copied a `CD0` mountlist
into `Devs/DOSDrivers` that named the emulator's own CD device, which would
have been wrong on the real machine the drive is for.

A real machine still needs a driver to read a CD. A stock Workbench 3.1 has the
filing system in `L:` and the `CD0` mountlist parked in `Storage/DOSDrivers`,
and the dialog says when the drive has none switched on.

The device it points at, `uaescsi.device` unit 0, is what FS-UAE presents a CD
on for a machine that has no CD drive of its own. If a disc still does not
appear, that value is the thing to check: it lives in
`Devs/DOSDrivers/CD0` on the drive and can be edited there.

## Method 1: stage it for installing later

This is the default, and for a multi-disc set it is usually the right answer.

**The discs are staged onto the drive itself, in `Storage/Install/<Title>`.** That is
the whole point of the mode. Boot the drive in an emulator, or put it in a real
Amiga, and the material is already in front of you: you can run the title's own
installer against the staging drawer on the machine the title will actually run
on. A staging directory on the computer running Amiga File Forge would be
unreachable at exactly the moment it was wanted.

`Storage` is where Workbench keeps what is not in use yet, which is what a
staged set is, and it is where the PiStorm imager puts the same thing. A plain
`Install` drawer at the volume root would have been the obvious choice, but the
AmigaOS Install disk is copied there by a Workbench install, and staging into
the same drawer listed that disk's own `c` and `Libs` as though they were
titles somebody had staged.

Stage the second disc under the same title and its files are merged into the
same tree, which is what an installer expects to be pointed at. Nothing is
emulated, nothing is downloaded, and nothing is guessed at, so this mode always
works and always works quickly.

Where two discs carry the same path with genuinely different contents, the first
is kept and the later one is filed under
`Storage/Install/Forge-Staging/<Title>/<Disc>`.
A set is never silently reduced to whichever disc you staged last. The staging
summary lists every conflict so you can see what happened.

Nothing belonging to Amiga File Forge is put inside the payload drawer, because
that drawer has to be exactly what gets installed. The record of what was
staged, and the files a later disc disagreed about, both live in
`Storage/Install/Forge-Staging`, which can be deleted once a set is in.

Protection bits and comments are written onto the volume with the files, because
an Amiga filing system has somewhere to put them. A loader that lost its `e` bit
will not start, and the failure looks nothing like a missing permission, so this
matters more than it sounds.

Staging a disc under a label that is already there replaces it, files and all.
Re-staging Disk 1 after correcting it leaves you with one Disk 1 holding the
corrected files, and any conflict previously recorded against that disc is
dropped. A set that grew every time it was fixed, or that filed your correction
away as an alternative to the broken file, would be impossible to reason about
by the time you came to install it.

Come back to a set with **Tools -> Staged installations**, with the drive open.
That lists every title waiting on it, what discs it holds and where its files
are, and installs one into a drawer you name. The list is read off the drive
rather than out of a record kept on this computer, so a drive built on another
machine, or one whose staging notes were deleted, still reports what is sitting
in its staging drawer.

Installing moves the title out of the staging drawer into its own home on the
same volume. Because both ends are on one drive it is a move rather than a copy:
nothing is read or written twice, and the protection bits, comments and
datestamps the discs carried are the ones already there. It also names any file
that differed between discs, so a set that needed a judgement call says so
rather than looking complete.

Discarding a title deletes the staged copies from the drive. The original disc
images are untouched, so the set can be staged again.

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
working Workbench on it before this is useful; see
[Preparing the drive](#preparing-the-drive-install-workbench). It also needs a Kickstart ROM for
the machine in your hardware profile; see the [firmware notes](../firmware/README.md).

Whichever mode you choose, the disc is staged first. An install that fails
halfway has still preserved the disc's contents somewhere you can finish by hand.

## What gets an undo point

Staging a disc, installing a staged title, installing Workbench, installing
WHDLoad and placing a slave all change a volume, and each takes an undo
checkpoint before it runs. Staging now takes one too, because it writes onto the
drive being built rather than into a directory on this computer. Booting the
emulator changes nothing Amiga File Forge owns, so it takes none.

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
