(() => {
  "use strict";
  function create({ showModal, modalContent }) {
function showHelp() {
  showModal(`
    <div class="help-guide">
      <div class="help-heading">
        <div><small>AMIGA FILE FORGE HANDBOOK</small><h2>How to use Amiga File Forge</h2></div>
        <p>Practical instructions for creating, editing, transferring, checking and saving Amiga media images.</p>
      </div>
      <div class="help-layout">
        <nav class="help-toc" aria-label="Help topics">
          <strong>START HERE</strong>
          <a href="#help-start">Open or create an image</a>
          <a href="#help-desktop">Linux desktop application</a>
          <a href="#help-workspace">Workspace and selection</a>
          <a href="#help-checkpoints">Undo and checkpoints</a>
          <a href="#help-files">Files and folders</a>
          <strong>MEDIA GUIDES</strong>
          <a href="#help-ofs">ADF and ADZ</a>
          <a href="#help-hfe">HFE, SCP and HxCFE</a>
          <a href="#help-rom">ROM images</a>
          <a href="#help-kickfs">Kickstart ROM data ROMs</a>
          <a href="#help-hdf">Hard drives and partitions</a>
          <a href="#help-ffs">FFS and AmigaOS</a>
          <a href="#help-hardfile">Hardfile HDA/GEO</a>
          <a href="#help-DMS archives">DMS archives</a>
          <strong>WORKFLOWS</strong>
          <a href="#help-online">Find and install online software</a>
          <a href="#help-transfer">Copy and drag between panes</a>
          <a href="#help-maintenance">Check and compact</a>
          <a href="#help-hex-editor">Raw image hex editor</a>
          <a href="#help-analysis">Workbench and analysis</a>
          <a href="#help-deployment">Hardware deployment</a>
          <a href="#help-saving">Save, close and recover</a>
          <a href="#help-shortcuts">Keyboard shortcuts</a>
          <a href="#help-accessibility">Accessibility and appearance</a>
          <a href="#help-limits">Limits and troubleshooting</a>
          <a href="#help-project">Project and support</a>
        </nav>
        <div class="help-content">
          <section id="help-start">
            <h3>Open or create an image</h3>
            <p class="help-lead">Edits are made to a private working copy. The file you selected on your computer is never overwritten.</p>
            <div class="help-note"><strong>Start small:</strong> a new workspace opens with one full-workspace pane. Select <strong>Add Pane</strong> whenever you need another source, destination or scratch image. There is no fixed pane-count limit, and extra panes open as cascading windows.</div>
            <div class="help-workflow" aria-label="Typical Amiga File Forge workflow">
              <span><b>1</b><strong>Open or create</strong><small>A private working image</small></span><i>→</i>
              <span><b>2</b><strong>Browse and edit</strong><small>Files, partitions and drawers</small></span><i>→</i>
              <span><b>3</b><strong>Analyse</strong><small>Structure, filesystems and launchers</small></span><i>→</i>
              <span><b>4</b><strong>Save</strong><small>Timestamped ZIP and README</small></span>
            </div>
            <div class="help-task">
              <h4>Open an existing image</h4>
              <ol>
                <li>Choose any empty pane.</li>
                <li>Select <strong>Open image</strong>, or drag a disk, dms or ROM image from your computer onto the empty pane.</li>
                <li>Choose the image. Supported families include ADF, ADZ, HFE, SCP, IPF, HDF, FFS floppy and hard-drive images, HDA/GEO, HDD, IMG, RAW, BIN and DMS. ZIP distributions can contain one supported image or a matched HDA/GEO pair.</li>
                <li>Wait for the opening indicator. The catalogue appears when identification is complete, or a hard drive's partition table when the image carries a Rigid Disk Block.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Create a new image</h4>
              <ol>
                <li>Open <strong>File → New → New Image (current format)</strong>. The current pane format is preselected.</li>
                <li>An existing empty pane is used first. If every pane contains an image, another workspace window is added automatically. Existing work is not replaced.</li>
                <li>Choose an OFS or FFS floppy in any of its DOS types (plain, international or directory cache), a DS/DD or high-density geometry, an HFE-wrapped floppy, a Hardfile HDA with its GEO sidecar, an RDB hard drive, or a RAW physical-drive image.</li>
                <li>Enter a disk title. For HDA, HDF and RAW images, enter a capacity such as <code>20MB</code> or <code>512MB</code>.</li>
                <li>The size field is read-only for the fixed floppy geometries, because an Amiga DS/DD floppy is always 880 KiB and the high-density format an A3000 or A4000 drive writes is always 1760 KiB. It becomes editable for Hardfile, RDB and RAW hard drives and remembers the last capacity you entered.</li>
                <li>The target is disabled when it does not apply, fixed to Hardfile for HDA/GEO, and fixed to Amiga 4000 / AmigaOS for RDB or RAW. Floppies retain a target choice because the same 880 KiB disk is used by every Amiga from the 1000 to the 4000.</li>
                <li>A partitioned drive takes its title from the volume in its first partition, so that is the name you enter here.</li>
                <li>Select <strong>Create image</strong>. The formatted image opens immediately as an editable working copy.</li>
                <li>Add content, then use the <strong>Save Image</strong> button in the pane heading to download it.</li>
              </ol>
            </div>
            <div class="help-note"><strong>Pane heading actions:</strong> after the orange changed indicator, the buttons create a New Blank Image, Load New Image, Save Image, Refresh View, Minimise, Maximise or restore, and Close Pane. The × close button offers Save and close, Close without saving, or Cancel whenever the image has changes.</div>
            <h4>Which new format should I choose?</h4>
            <div class="help-table-wrap"><table class="help-table"><caption class="visually-hidden">Supported image formats and their main limits</caption>
              <thead><tr><th>Format</th><th>Best used for</th><th>Important limit</th></tr></thead>
              <tbody>
                <tr><td>ADF · OFS</td><td>A Kickstart 1.3 floppy, DOS\\0 or DOS\\2</td><td>880 KiB DS/DD; 488 usable bytes per 512-byte block</td></tr>
                <tr><td>ADF · FFS</td><td>A Kickstart 2.0 or later floppy, DOS\\1, DOS\\3 or DOS\\5</td><td>880 KiB DS/DD; not readable by Kickstart 1.3</td></tr>
                <tr><td>High-density ADF</td><td>An A3000 or A4000 HD drive</td><td>1760 KiB; the rest of the range reads DS/DD only</td></tr>
                <tr><td>ADZ</td><td>A gzip-compressed ADF, as distributed online</td><td>Expanded before it is opened</td></tr>
                <tr><td>DMS</td><td>A DiskMasher archive of a whole floppy</td><td>Read-only; every DiskMasher compression mode is decoded</td></tr>
                <tr><td>HFE / SCP</td><td>HxC, Gotek and flux-level floppy workflows</td><td>Protected layouts open read-only</td></tr>
                <tr><td>HDA + GEO</td><td>A UAE hardfile with its geometry sidecar</td><td>Downloads as a ZIP containing the required pair</td></tr>
                <tr><td>HDF with RDB</td><td>A partitioned hard drive an Amiga will mount</td><td>Choose enough capacity before creating</td></tr>
                <tr><td>IPF</td><td>An SPS preservation capture of a protected disk</td><td>Read-only; needs the SPS decoder library</td></tr>
                <tr><td>RAW</td><td>A whole physical drive image</td><td>Bytes are exactly what the drive holds</td></tr>
                <tr><td>ROM</td><td>Kickstart, cartridge and expansion ROM images</td><td>Bytes are not assumed to be files</td></tr>
                <tr><td>Kickstart ROM filing system</td><td>Files stored inside a ROM image</td><td>Flat and case-sensitive; needs a companion module</td></tr>
              </tbody>
            </table></div>
          </section>
          <section id="help-desktop">
            <h3>Linux desktop application</h3>
            <p class="help-lead">The Linux edition is the same Amiga File Forge workbench in a native GTK 4 window. Format support, editors, validation and saved packages stay aligned with the Docker edition.</p>
            <div class="help-task">
              <h4>Install and launch</h4>
              <ol>
                <li>Stable releases provide separate Debian 13 and Ubuntu 24.04 packages for AMD64, ARM64 and ARMv7. Install the matching <code>.deb</code> with APT, for example <code>sudo apt install ./amiga-file-forge_1.0.2-1~deb13_amd64.deb</code>. APT installs the required Python 3, GTK 4, Libadwaita, WebKitGTK 6 and GObject packages.</li>
                <li>For development from a project checkout, install those system packages and run <code>tools/install-linux-desktop.sh</code> instead.</li>
                <li>Launch <strong>Amiga File Forge</strong> from the application menu. The package command is <code>amiga-file-forge</code>; a checkout uses <code>tools/amiga-file-forge-desktop</code>.</li>
                <li>Use the native folder button, <strong>File → Open image</strong> in a pane or <kbd>Ctrl</kbd>+<kbd>O</kbd> to select one or several images with the GTK chooser. You can also drag image files from the Linux file manager onto a pane. Native selection and drag and drop pass local paths to the private desktop service, so image bytes are not uploaded through the embedded browser.</li>
                <li>Drop an HDA and its matching GEO together, or select either member when its companion is beside it. Multiple ADF, ADZ, HFE, DMS, HDF, ROM and other recognised images open into successive available panes.</li>
                <li>Review the selection before it opens. The active Workbench profile supplies the initial FFS target, which can be changed for this operation. Multiple ROM files may be opened separately or treated as one linear or byte-interleaved physical component set.</li>
              </ol>
            </div>
            <ul>
              <li>HDA and GEO partners with the same basename are paired automatically.</li>
              <li>Chooser, file-association and file-manager selections use one serial opening queue, so two large images cannot race while their private sessions are created.</li>
              <li>The selected source is cloned by the filesystem when possible, or sparse-copied once into XDG application storage. The original is not changed in place.</li>
              <li>Large Hardfile HDA files bypass browser upload and spooling. The former pause near 24 percent was the embedded browser transferring the HDA, not FFS analysis. Expensive hardware finalisation remains deferred to Save and reports its stages there.</li>
              <li>GTK and Libadwaita supply the title bar, window controls, application menu, chooser and symbolic header icons. The workbench inherits the desktop font and initially follows the system light or dark setting.</li>
              <li>A stable private owner recovers this Linux user's working sessions. Workspace settings, hardware profiles and the private collection catalogue are stored atomically under the XDG configuration directory, so a new random-port launch does not lose them.</li>
              <li>Saved ZIPs use the normal Linux Downloads directory and contain the same image, sidecars and README as the web edition.</li>
              <li>Managed emulators appear as native windows. In Docker they continue to appear in the browser display.</li>
              <li>Install the optional official Greaseweazle tools to write ADF, ADZ, FFS floppy and HFE images to real disks.</li>
            </ul>
            <div class="help-task"><h4>Write a physical floppy with Greaseweazle</h4><ol>
              <li>Confirm <code>gw info</code> can see the connected device and that the Linux udev rules permit access.</li>
              <li>Open a supported floppy image.</li>
              <li>Choose <strong>Tools → Write physical floppy</strong>, or right-click the image title or coloured format badge.</li>
              <li>Select drive A, B, 0, 1, 2 or 3, insert the destination disk, then acknowledge that every existing byte on it will be overwritten.</li>
              <li>Follow the live cylinder, head and verification progress. Abort stops Greaseweazle, but leaves the physical disk potentially incomplete; the working image remains unchanged.</li>
              <li>Keep a sector disk only after the completion dialog confirms verification. HFE contains raw bitcells and cannot be automatically verified, so test that disk on suitable hardware.</li>
            </ol></div>
            <div class="help-note"><strong>Stable source:</strong> the working image is finalised and copied to a private snapshot before the physical write begins. Later edits cannot alter an in-progress disk, and the temporary image and its snapshot are removed afterwards.</div>
            <div class="help-note"><strong>One product, two hosts:</strong> the desktop application embeds the shared frontend and starts the shared API on a private random loopback port. A fresh launch token protects it, a separate mode-0600 owner identifies its sessions, and native-only path and state adapters are not exposed by the web host.</div>
            <div class="help-note"><strong>Icon opens no window:</strong> pull the current code and rerun <code>tools/install-linux-desktop.sh</code>. The launcher detects Ubuntu systems that deny WebKitGTK's Bubblewrap user namespace and enables the compatibility fallback only there. Set <code>AMIGA_FILE_FORGE_DISABLE_WEBKIT_SANDBOX=0</code> to require sandboxing or <code>1</code> for diagnostic fallback. Run <code>~/.local/bin/amiga-file-forge</code> in a terminal if startup diagnostics are still needed.</div>
            <div class="help-warning"><strong>Updating:</strong> install a newer <code>.deb</code> over the release package, or pull the new source and rerun the checkout installer when Python dependencies change. Working sessions remain under <code>~/.local/share/amiga-file-forge</code>, or the configured XDG data directory.</div>
          </section>
          <section id="help-workspace">
            <h3>Workspace, navigation and selection</h3>
            <figure><img src="/help/workspace.png" alt="Amiga File Forge showing movable image panes and the Add Pane control"><figcaption>The workspace begins with one pane. Add and arrange as many movable image windows as the computer can comfortably display; each retains independent navigation, selection, refresh, progress and save controls.</figcaption></figure>
            <h4>Add, arrange and close panes</h4>
            <ol>
              <li>Select <strong>Add Pane</strong> in the header to add an empty cascading window. There is no fixed pane-count limit.</li>
              <li>Drag an empty part of a pane heading, or use the numbered grip at its left, to move it. Windows may overlap, and selecting any part of a window brings it to the front.</li>
              <li>Drag a pane to the left or right edge to fill that half, to a corner to fill that quarter, or to the top edge to maximise it. The translucent preview shows the result before release.</li>
              <li>Drag any pane edge or corner to resize it. A snapped pane begins resizing from its visible snapped rectangle rather than jumping back to its earlier size. The lower-right corner has a visible resize mark. Double-click the numbered grip or use the square heading button to maximise or restore it.</li>
              <li>When the browser or workspace changes size, free panes scale proportionally to remain useful and visible. Snapped panes continue to follow their selected side or corner.</li>
              <li>Select the line button to minimise a pane to the shelf at the bottom of the workspace. Select its shelf button to restore and focus it.</li>
              <li>With the numbered grip focused, use Alt+Left or Alt+Right to snap, Alt+Up to maximise, and Alt+Down to minimise without a pointer. Hold Shift as well to resize in 32-pixel steps.</li>
              <li>An empty pane is a convenient scratch area for creating an ADF, ADZ, HDF, FFS floppy, Hardfile HDA/GEO pair or other supported image.</li>
              <li>Select × at the top-right to close that whole pane. Save changed images from the prompt, deliberately close without saving a download, or cancel. The server working copy remains available through Recovery.</li>
              <li>Open images, positions, sizes, snap layout, stacking order and minimised windows are remembered across a normal page refresh. A completely fresh workspace starts with one pane.</li>
            </ol>
            <div class="help-note"><strong>Two different drag operations:</strong> drag a heading or its numbered grip to move or snap the window. Drag file rows or the coloured format badge on a supported disk image to transfer content between images.</div>
            <div class="help-note"><strong>Familiar pane menus:</strong> File and Edit are always first, followed by View, Library, Analyse and Tools. File holds open, save, add and create commands. Edit holds Cut, Copy, Paste, Undo and Checkpoints. View holds refresh and the command that returns to a drive&rsquo;s partition table. The heading icons remain quick shortcuts for common image actions.</div>
            <div class="help-note"><strong>Free-space meter:</strong> the lower-right bar uses the image filesystem's real allocation data. Green means under 70% used, orange means 70% or more, and red means 90% or more. Hover over it for used, free and total values. A drive's partition table counts allocated space; opening a partition switches the meter to that volume's OFS bytes. DMS archives have no fixed free-space capacity and show a neutral striped meter.</div>
            <h4>Navigate an image</h4>
            <ol>
              <li>Double-click a directory to enter it. Double-click a file to open the BASIC, script, text, disassembly or hex editor selected from its contents.</li>
              <li>Double-click <strong>..</strong> to move to the parent directory, or select any breadcrumb to jump directly to that location.</li>
              <li>Inside a partition, use <strong>All partitions</strong> to return to the partition table. The partition you left remains selected and is scrolled back into view.</li>
              <li>Select ↻ in the pane heading to reread the current directory or partition table without closing the image.</li>
              <li>Click the image filename in the pane heading to edit it. Press <kbd>Enter</kbd> or click elsewhere to save, or press <kbd>Escape</kbd> to cancel. The format extension is retained; HDA/GEO pair names stay matched. This renames the recovered and downloaded container, not its internal disk title.</li>
            </ol>
            <h4>Select one or several items</h4>
            <ol>
              <li>Click an item to select only it.</li>
              <li>Use <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>-click to add or remove individual items.</li>
              <li>Use <kbd>Shift</kbd>-click to select the range between the anchor and the clicked row.</li>
              <li>Press <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>-<kbd>A</kbd> while a row has focus to select every usable item in the current view.</li>
              <li>Start dragging any selected row to carry the complete selection.</li>
              <li>Point at a single row to reveal Rename and Delete beside its name. For a multiple selection, Rename is hidden and Delete applies to the whole selection with one confirmation.</li>
              <li>The Access column reveals separate read/write and read-only controls. They apply to one file or disk, or every applicable item in a multiple selection.</li>
            </ol>
            <div class="help-note"><strong>The orange dot means changed:</strong> the working image contains edits not yet downloaded. It clears after Save Image has successfully prepared the download and returns after the next edit. A failed save leaves the dot visible. It does not mean the original file has changed.</div>
          </section>
          <section id="help-checkpoints">
            <h3>Undo changes and create named checkpoints</h3>
            <p class="help-lead">Every image-changing operation starts with an automatic restore point. This includes file and directory edits, transfers, compaction and save-time image finalisation.</p>
            <div class="help-task">
              <h4>Undo the latest operation</h4>
              <ol>
                <li>Open <strong>Edit</strong> in the affected pane.</li>
                <li>Select <strong>Undo last change</strong>. The button is disabled until an automatic restore point exists.</li>
                <li>Confirm the undo. The most recent automatic point is restored and consumed.</li>
                <li>All panes showing that same image return to its root, or to a drive&rsquo;s partition table, and refresh from the restored bytes.</li>
                <li>Repeat to step backwards through earlier operations. Up to 20 recent automatic points are retained per image.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Create and restore a named checkpoint</h4>
              <ol>
                <li>Before a large reorganisation, open <strong>Edit → Checkpoints</strong>.</li>
                <li>Enter a useful name such as <code>Before reorganising Workbench</code>, then select <strong>Create named checkpoint</strong>.</li>
                <li>Return to the same dialog at any time to inspect named checkpoints and automatic undo points.</li>
                <li>Select ↶ beside a checkpoint and confirm to restore it. The state being replaced is first retained as a new automatic undo point.</li>
                <li>Select × beside an unwanted checkpoint to delete only that snapshot.</li>
              </ol>
            </div>
            <div class="help-note"><strong>Large HDD images:</strong> Amiga File Forge asks the host filesystem for a copy-on-write clone. If cloning is unavailable, its safe-copy fallback preserves sparse zero ranges instead of writing unused HDA capacity. Either form remains a complete byte-for-byte restore point.</div>
            <div class="help-warning"><strong>Checkpoints belong to the working session:</strong> they are private to the same browser owner and survive refreshes and container restarts, but clearing the recovered session or deleting the Docker work volume removes them too. Download important finished images separately.</div>
          </section>
          <section id="help-files">
            <h3>Create, modify and delete files and folders</h3>
            <div class="help-task">
              <h4>Add one or more host files</h4>
              <ol>
                <li>Navigate the destination pane to the required OFS catalogue or FFS directory.</li>
                <li>Open <strong>File → Insert File</strong> and choose one or more files.</li>
                <li>For each file, review the target name, protection bits and file comment.</li>
                <li>If a name is illegal for the target filing system, accept the safe suggestion or type a valid replacement.</li>
                <li>Select <strong>Insert File</strong> in the dialog. Each successful insertion appears in the current view.</li>
                <li>For a multiple selection, choose <strong>Insert and apply to all remaining</strong> to accept each later file's own detected name and metadata without reopening the same review.</li>
              </ol>
              <p>Files copied from one Amiga image to another retain their protection bits, comment and datestamp. A loose host file carries none of those, so select its companion <code>.inf</code> sidecar as well when an Amiga tool wrote one. Raw host bytes alone say nothing about how a file was protected.</p>
            </div>
            <div class="help-task">
              <h4>Inspect or change protection, comment and date</h4>
              <ol>
                <li>At file level, read the <strong>Protection</strong>, <strong>Comment</strong> and <strong>Date</strong> columns. These are the three things AmigaDOS records about a file, and they come from the image rather than being guessed from its bytes.</li>
                <li>Protection is shown the way <code>List</code> shows it, as the eight letters <code>hsparwed</code>. A letter means the bit is set; a dash means it is not.</li>
                <li>The low four bits are stored inverted, so an ordinary file that can be read, written, executed and deleted stores <code>0x00</code> and reads <code>----rwed</code>, while a locked one stores <code>0x05</code> and reads <code>----r-e-</code>. The editor works in the letters, because the inversion is where mistakes are made.</li>
                <li>Select the protection value to change it. The comment is up to 79 characters and the datestamp is the volume's own, counted from 1 January 1978.</li>
                <li>Changes are written into the file header. The file's own bytes are not rewritten.</li>
              </ol>
              <div class="help-note"><strong>Protection is advice, not enforcement.</strong> Kickstart honours the delete bit and little else, so a read-only file is a message to whoever opens the disk as much as to the software. The workbench refuses its own writes to a protected file so that message is not silently ignored.</div>
            </div>
            <div class="help-task">
              <h4>Import one or more host folders</h4>
              <ol>
                <li>Navigate to the destination and choose <strong>File → Insert Folder &amp; Contents</strong>, or drag folders from the desktop onto the pane. Use drag and drop to select several top-level folders when your browser supports it.</li>
                <li>Review the preflight. Desktop housekeeping files are ignored and any target-name shortening is shown before the image changes.</li>
                <li>On FFS, keep <strong>Preserve folder structure</strong> to recreate the tree under the current directory, or choose <strong>Import all files here</strong> to flatten it.</li>
                <li>Drawers are preserved, because every AmigaDOS volume nests them: OFS and FFS differ in how they store a file's data, not in how they store a directory.</li>
                <li>Tick the explicit replacement option only when existing ordinary files with the same target paths should be overwritten.</li>
                <li>When a later review repeats the same decision, use <strong>Apply to all remaining</strong>. Every item keeps its own detected filename, protection bits, comment and datestamp.</li>
              </ol>
              <p>The complete batch uses one filesystem mount and one undo checkpoint, which is substantially quicker and safer than adding every small file separately.</p>
            </div>
            <div class="help-task">
              <h4>Create an FFS directory</h4>
              <ol>
                <li>Navigate to the parent directory.</li>
                <li>Choose <strong>File → New → New folder</strong>, enter a legal name and select <strong>Create folder</strong>.</li>
                <li>Double-click the new directory to enter it, then add or drag content into it.</li>
              </ol>
              <p>FFS directories are real hierarchical objects. OFS uses the separate catalogue-group workflow below.</p>
            </div>
            <div class="help-task">
              <h4>Work with drawers</h4>
              <ol>
                <li>An ADF, ADZ side or open HDF disk starts directly on <strong>$</strong>. Default-catalogue files appear first.</li>
                <li>After a visual gap, populated A-Z groups appear below as complete OFS names such as <strong>R.GAME</strong>. Each prefix stays grouped like a catalogue listing, but it is still part of the same flat OFS catalogue.</li>
                <li>Choose <strong>File → New → New folder</strong> to create a drawer, and open it like any other directory.</li>
                <li>An empty group cannot be saved because OFS stores the prefix on each file, not as a separate directory entry.</li>
                <li>Files from every displayed prefix can be opened, downloaded, renamed, protected, copied or deleted without changing views.</li>
                <li>At a partition's root, double-click <strong>..</strong> to return to <strong>All partitions</strong>.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Rename or move an item</h4>
              <ol>
                <li>Point at a file or directory and select its pencil icon to rename it in place.</li>
                <li>Enter a legal leaf name and select <strong>Rename</strong>.</li>
                <li>On FFS, move an item by dragging its row onto a directory. To move several items together, select them first and drag any selected row.</li>
                <li>You can also open the same FFS image in multiple panes, navigate each pane independently, then drag into the required destination pane.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Change access, download or delete</h4>
              <ol>
                <li>Point at the Access column and select ◇ for read/write or ◆ for read-only. Select several files first to update them together.</li>
                <li>Use the download arrow beside an ordinary file to download a ZIP containing the loose file and its matching <code>.inf</code> metadata sidecar without changing the image. The sidecar retains the real path, protection bits, length and comment. DMS members, and archive members carrying either Amiga ZIP protection bits or a companion <code>.inf</code>, use the same bundle. Double-click opens the appropriate editor.</li>
                <li>To remove one or several items, select them and use any visible × on the selected rows, or press <kbd>Delete</kbd>.</li>
                <li>Read the single confirmation carefully. Deleting an FFS directory recursively removes everything below it.</li>
              </ol>
            </div>
          </section>
          <section id="help-ofs">
            <h3>ADF and ADZ: complete workflow</h3>
            <div class="help-task">
              <h4>Create and populate an OFS disk</h4>
              <ol>
                <li>Create an ADF for one 880 KiB DS/DD volume, or a high-density ADF for 1760 KiB.</li>
                <li>In a file holding two volumes, such as a two-disk set dumped as one image, use <strong>Volume 1</strong>/<strong>Volume 2</strong> to choose the one you are editing.</li>
                <li>The pane opens on the volume root. Drawers appear as directories and open like any other. Use <strong>File → New → New folder</strong> to create one.</li>
                <li>Use <strong>File → Insert File</strong>, or drag selected files from another pane or onto a drawer row.</li>
                <li>Review shortened names, protection bits and comments before confirming each import.</li>
                <li>Use the row actions to rename or delete. Use the Access column to mark one or several files read/write or read-only.</li>
                <li>Use <strong>Tools → Check filesystem</strong>, optionally compact it, then select <strong>Save Image</strong> in the pane heading.</li>
              </ol>
            </div>
            <h4>AmigaDOS rules enforced by the app</h4>
            <ul>
              <li>A name holds at most 30 characters, and cannot contain <code>:</code>, <code>/</code> or <code>\\</code>. A full stop is an ordinary character.</li>
              <li>The pane opens on the volume root. Drawers nest to any depth, on OFS exactly as on FFS.</li>
              <li>An AmigaDOS directory is a hash table with overflow chains, so free blocks are the only real limit on how many entries it holds.</li>
              <li>OFS stores 488 bytes of a file in each 512-byte block; FFS stores the whole 512, which is why an FFS volume holds more.</li>
              <li>A file must fit in the remaining free blocks. Compacting consolidates fragmented free space so a large file can be written contiguously.</li>
              <li>A volume can be extracted into a drawer on another volume, keeping its drawer structure.</li>
            </ul>
            <div class="help-note"><strong>To copy a whole disk into a hard drive:</strong> drag the disk-format badge or the open pane heading onto the destination pane. Choose a drawer name, and the volume is extracted there.</div>
          </section>
          <section id="help-hfe">
            <h3>HFE and SCP flux images: safe decoding and export</h3>
            <figure><img src="/help/hfe-create.png" alt="Create image dialog showing HFE-wrapped OFS and FFS floppy choices"><figcaption>Create a new HFE around a DS/DD or high-density Amiga floppy, formatted OFS or FFS. Existing supported HFE images open through the normal image picker.</figcaption></figure>
            <p>HFE stores floppy track timing and bit cells, while OFS and FFS describe files inside the sectors. Amiga File Forge uses the official HxCFloppyEmulator command-line converter, <code>hxcfe</code>, to decode those sectors and then opens the detected filing system. A supported HFE is not merely recognised: its decoded catalogue is browseable through the normal pane. Docker images and native Debian/Ubuntu packages include a pinned, architecture-native HxCFE build and its supporting libraries. No separate HxC installation is required.</p>
            <ol>
              <li>Open an HFE normally, or create an HFE-wrapped OFS/FFS floppy from <strong>File → New → New Image</strong>.</li>
              <li>Check the opening warning. A clean HFE v1 disk is editable through the usual file tools.</li>
              <li>HFE v2/v3, weak-bit, bad-sector, protected or advanced timing images open as a clearly labelled read-only safe view. Export or drag files from them without changing their tracks.</li>
              <li>For an editable HFE, make the required changes and select <strong>Save Image</strong> in the pane heading.</li>
              <li>The app writes changed sectors into a copy of the original track layout, decodes that result, and compares every sector with the working filesystem. A mismatch blocks the download and leaves the original HFE intact.</li>
            </ol>
            <div class="help-note"><strong>What the pane shows:</strong> the format badge reads HFE, while the directory rules, geometry and capacity come from its decoded OFS or FFS filesystem. Advanced images show <strong>Read-only safe view</strong> and hide editing and compaction controls.</div>
            <div class="help-note"><strong>Transfers:</strong> any supported HFE filesystem can be opened in one pane and copied or extracted into another image. A sector image holds only sectors, so the timing, weak-bit and protection information an advanced HFE carries is deliberately omitted and reported as a destination warning.</div>
            <div class="help-task"><h4>How saving is verified</h4><ol>
              <li>HxCFE encodes the changed sectors into a new HFE, using the original track layout as a reference where applicable.</li>
              <li>HxCFE decodes the candidate output again.</li>
              <li>Amiga File Forge byte-compares the decoded result with the complete working filesystem.</li>
              <li>A mismatch blocks the download and preserves the original HFE. A successful HFE is added to the timestamped save package.</li>
            </ol></div>
            <div class="help-note"><strong>Installed Linux runtime:</strong> the private HxCFE executable is <code>/opt/amiga-file-forge/native/bin/hxcfe</code> and its libraries are under <code>/opt/amiga-file-forge/native/lib</code>. The application launcher configures that library path automatically.</div>
            <div class="help-task"><h4>Open a Greaseweazle or SuperCard Pro SCP capture</h4><ol>
              <li>Open or drag the <code>.scp</code> file onto the workspace. The same picker works in the web and Linux desktop editions.</li>
              <li>Wait while HxCFE decodes the flux revolutions. Amiga File Forge identifies the recovered sectors as OFS or FFS, restores a single omitted blank tail sector when HxCFE exhibits its known final-sector behaviour, then validates the complete filesystem.</li>
              <li>Browse the normal catalogue and directory hierarchy. The pane badge remains <strong>SCP</strong>, while the file rules follow the recovered filesystem.</li>
              <li>Select <strong>File → Export as…</strong>, or the <strong>Export</strong> control in the pane header between <strong>Save Image</strong> and <strong>Refresh View</strong>, and choose the native sector image. A recovered DS/DD disk downloads as a 901,120-byte <code>.adf</code> image, or as the same image gzipped to <code>.adz</code>. The header control is greyed out when the open media has no compatible target.</li>
            </ol></div>
            <div class="help-note"><strong>Safety boundary:</strong> recognition of a root directory is not enough. The app rejects an SCP capture if the full Amiganut filesystem validator finds a broken map, catalogue or directory tree. Non-standard index timing is reported but does not block a capture whose recovered sectors validate completely.</div>
            <div class="help-note"><strong>Editing:</strong> before enabling writes, the app re-encodes the recovered sectors to SCP and decodes them again. A byte difference makes the capture read-only. Read-only captures can still be browsed, analysed, copied into another image and exported as a native ADF, ADZ, ADS, ADM, ADL or ADF sector image.</div>
          </section>
          <section id="help-rom">
            <h3>ROM images: banks, headers and chip sets</h3>
            <p class="help-lead">A ROM is a byte image rather than a filing system. The pane divides it into explicit banks without changing the saved bytes.</p>
            <figure><img src="/help/rom-pane.png" alt="ROM pane showing bank address, decoded identity, purpose, entry points and programmed utilisation"><figcaption>The main pane is a bank inventory, not a directory. At narrow pane widths each bank becomes a readable two-column card while retaining the same decoded fields.</figcaption></figure>
            <div class="help-note"><strong>Terms used in the pane:</strong> <em>Bank</em> is the zero-based logical block selected by the configured bank size. <em>File address</em> is its byte offset in the complete image. <em>Mapped address</em> is the conventional CPU window for the selected target. <em>Programmed</em> means bytes that differ from the configured erased value; it is not filesystem free space.</div>
            <div class="help-task"><h4>Open and inspect a ROM</h4><ol>
              <li>Open a <code>.rom</code>, numbered <code>.rom0</code> to <code>.rom7</code>, or a <code>.bin</code> carrying a recognised Amiga ROM header. For a headerless or generically named dump, choose the Amiga ROM raw-format override in the open dialog.</li>
              <li>The default view uses 16 KiB banks. Choose <strong>Tools → ROM layout</strong> if the device uses 8K, 32K or another 256-byte-aligned bank size.</li>
              <li>Read the bank inventory from left to right: bank and image address, decoded identity, purpose and entry points, then programmed contents. An Amiga-family bank also shows its mapped CPU window. Empty and unrecognised banks are labelled plainly.</li>
              <li>The guidance strip above the inventory explains the shortcuts. Select ⓘ for decoded information, double-click for Hex, or open <strong>Tools → ROM Workbench</strong> for disassembly, comparison and hardware preparation.</li>
              <li>Image Health recognises Amiga-family headers and the standard AmigaOS <code>ExtnROM0</code> trailer. A bad AmigaOS extension-ROM checksum is reported as a failure.</li>
              <li>Select ⓘ on a bank to open its decoded-content view. It shows header fields, processor type, declared feature bits, mapped entry points, known regions and bounded printable strings with their byte offsets and mapped addresses.</li>
              <li>Use <strong>Resident modules</strong> in that view to see the libraries, devices and resources the ROM makes available, with the node type, version and priority each tag declares.</li>
              <li>A <strong>?</strong> beside a module shows the identification string its tag points at. Point at it, focus it from the keyboard, or select it to keep the tooltip open. The source label distinguishes a declared tag, a signature reconstructed from a table, and a literal string recovered from the ROM. Press <kbd>Escape</kbd> to close pinned help.</li>
              <li>An Amiga expansion ROM has no single standard module catalogue. The app recognises coherent resident-module name and vector tables. A vector table must also have a 68000 code reference and valid handlers inside the ROM's own address window.</li>
              <li>Printable text alone is not included, because help text, examples and even machine code can resemble a name. A vector table provides separate Table and Handler buttons, and a name table links to its own entry. These links open a hex editor inside the decoded-information dialog; closing it reveals the same information at its previous scroll position. Hex editing opened from a pane menu stays inside that pane.</li>
              <li>If no module is shown, the ROM may still install one: a tag can be built at run time, or laid out in a way the static scanner does not recognise. Check the machine's own module list on suitable hardware, or inspect the initialisation routine in the hex editor.</li>
              <li>Printable strings can also reveal messages and build information, but are labelled as evidence rather than guessed files. Every decoded location and command has a direct Hex button.</li>
              <li>The decoder also reports SHA-256, CRC-32, entropy, distinct byte values, erased space, used range, programming offsets and identical banks. Header flags are checked against the actual entry vectors.</li>
              <li>For an Amiga 4000 target, plausible AmigaOS module headers expose titles, help text, entry facilities and SWI information. They remain labelled as candidates unless the enclosing ROM structure proves them.</li>
              <li>A recognised Amiga, Master or Amiga 600 header shows its title, version and language/service roles. AmigaOS extension images show their <code>ExtnROM0</code> size and checksum trailer. Unknown custom data remains honestly labelled as raw code and data.</li>
              <li>Double-click a bank to open the hex editor at that bank's first byte. Use the image health dashboard to report partial banks and recognised headers.</li>
            </ol></div>
            <figure><img src="/help/rom-decoder.png" alt="Decoded Amiga-family ROM header, fingerprints and star-command table"><figcaption>The decoder separates proven header fields, byte statistics and structured command evidence. It opens with focus on the heading, not on the first command; Tab moves into the controls.</figcaption></figure>
            <figure><img src="/help/rom-command-help.png" alt="A pinned tooltip showing command syntax reconstructed from a ROM table"><figcaption>Command help states its source. Hover or keyboard focus shows it temporarily; select the question mark to pin it while reading.</figcaption></figure>
            <div class="help-note"><strong>Decoder boundaries:</strong> entropy, strings and command candidates are evidence, not a claim that code is safe or that strings are files. A missing command may be constructed dynamically. A plausible AmigaOS module remains a candidate until its enclosing structure proves it.</div>
            <div class="help-task"><h4>Create and edit a banked image</h4><ol>
              <li>Choose <strong>File → New → New Image (ROM)</strong>. Set the total byte size, logical bank size, target family, erased byte and layout.</li>
              <li>Choose erased bytes for a blank device, or the inert resident-tag skeleton for custom expansion-ROM development.</li>
              <li>Use <strong>File → Insert ROM bank(s)</strong> for one or several files. Exact-multiple combined images are split into consecutive banks; anything requiring silent truncation is refused.</li>
              <li>Rename edits a recognised header title. Erase fills selected banks with <code>&FF</code> or <code>&00</code> without shrinking the image. Append empty bank grows the image by exactly one configured bank.</li>
              <li>Use Cut, Copy, Paste or drag between ROM panes. Dragging inside one ROM is an atomic move and overlapping ranges are safe.</li>
            </ol></div>
            <div class="help-task"><h4>Open physical chip sets</h4><ol>
              <li>Select two or four equal-sized ROM component files together.</li>
              <li>Choose concatenate for consecutive banks, or byte interleave for byte-wide chips. Keep the displayed file order correct for the physical sockets.</li>
              <li>Four-way byte interleaving covers the usual Amiga 4000/AmigaOS ROM arrangement. The working pane shows logical byte order.</li>
              <li>The save ZIP keeps the logical image and reconstructs the individual component files under <code>ROM-components</code>. Its README records the original component names and order.</li>
            </ol></div>
            <div class="help-task"><h4>Analyse and compare ROM code</h4><ol>
              <li>Choose <strong>Tools → ROM Workbench</strong>. Overview shows every bank, its file offset, decoded identity, physical byte lanes and duplicate banks.</li>
              <li>Review the audit findings. The app can safely align contradictory ROM header flags with proven entry vectors and rebuild an extended-ROM checksum. An automatic undo point is made first.</li>
              <li>Open <strong>Disassembly</strong>, choose a bank, architecture, mapped origin and offset. Auto detect follows the processor the applied hardware profile implies, and the baseline 68000 when no profile is set.</li>
              <li>Every 68000-family processor is decoded big-endian, as the hardware reads it. Bytes that decode to no instruction remain visible as <code>DC.B</code> data. Known entry points seed reachable-code analysis, call and branch targets gain cross-references, and library calls through A6 are named.</li>
              <li>Save address labels under <strong>Project</strong> using <code>address = label</code>. Known regions use <code>start-end = meaning</code>. Disassemble again to apply them to the listing.</li>
              <li>To compare revisions, open the other ROM in another pane and select it under <strong>Compare</strong>. Download the guarded patch when required.</li>
              <li>Tick individual comparison ranges to export only reviewed changes. A patch is applied only when the complete source SHA-256 matches. The finished bytes must then match the stored target SHA-256 or the operation fails.</li>
              <li>Use <strong>Identify this exact ROM</strong> on Overview to add a private title, version, publisher and platform record. It is keyed by SHA-256 and scoped to the current browser owner.</li>
            </ol></div>
            <figure><img src="/help/rom-workbench-overview.png" alt="ROM Workbench Overview showing bank map, exact identity and audit findings"><figcaption>Overview relates logical banks to file offsets, decoded type and duplicates. Repairs appear only when the fault and replacement value are deterministic.</figcaption></figure>
            <figure><img src="/help/rom-workbench-disassembly.png" alt="ROM Workbench Disassembly showing architecture controls, decoded instructions, reachability and references"><figcaption>Disassembly is bounded static analysis. Select architecture, mapped origin, bank offset and byte count; saved project symbols and regions annotate later listings.</figcaption></figure>
            <div class="help-note"><strong>Workbench safety model:</strong> Overview, Disassembly and Compare are read-only. Identity, Project and Emulator store separate project metadata. Repair, patch application and Build change ROM bytes only after review and an automatic checkpoint. Programmer transforms affect only its downloaded ZIP.</div>
            <div class="help-task"><h4>Build and prepare ROMs</h4><ol>
              <li>Under <strong>Build</strong>, choose an expansion-ROM scaffold or a file archive, then review the replacement warning.</li>
              <li>The expansion-ROM scaffold has an inert initialisation routine. It is a development starting point and does not pretend that named commands already have implementations.</li>
              <li>The file archive packages named bytes for a companion resident module. Kickstart cannot mount it as a filing system on its own.</li>
              <li>Under <strong>Programmer</strong>, choose the physical device size, one, two or four byte lanes, and any required mirroring, adjacent-byte swapping, 16-bit word swapping or address-line swaps.</li>
              <li>Keep the generated programming report with the chip files and verify its checksum against a programmer read-back.</li>
              <li>The saved image ZIP includes <code>ROM-project.json</code> with notes, symbols and emulator results. These annotations never alter the ROM bytes.</li>
            </ol></div>
            <figure><img src="/help/rom-workbench-programmer.png" alt="ROM Workbench Programmer tab configured to mirror and split a ROM into two byte-wide chips"><figcaption>Programmer export applies padding or mirroring, byte and word transforms, address-line swaps, then physical lane splitting. Its report records checksums for programmer read-back.</figcaption></figure>
            <div class="help-task"><h4>Understand each Workbench tab</h4><ul>
              <li><strong>Overview:</strong> bank map, byte lanes, exact SHA-256 identity, audit and narrowly proven repairs.</li>
              <li><strong>Disassembly:</strong> 68000, 68010, 68020, 68030, 68040 or 68060 decoding with reachable-code analysis, cross-references, library call labels and project annotations.</li>
              <li><strong>Compare:</strong> contiguous revision differences and complete or selective patches guarded by source and target SHA-256.</li>
              <li><strong>Build:</strong> an inert Amiga expansion-ROM scaffold with a real resident tag, or an <code>AFFARCHIVE1</code> file archive for companion data. Neither is a finished application by itself.</li>
              <li><strong>Programmer:</strong> device padding or mirroring, adjacent-byte swaps, 16-bit word swaps, address-line swaps and one, two or four physical byte lanes.</li>
              <li><strong>Project:</strong> hardware notes, research, address labels and known regions stored outside the ROM bytes.</li>
              <li><strong>Emulator:</strong> the managed emulator selected by the applied hardware profile. Direct ROM attachment is enabled only when the target machine's slot mapping is safe.</li>
            </ul></div>
            <div class="help-task"><h4>Run a configured emulator check</h4><ol>
              <li>Choose a machine and emulator in <strong>Workbench → Hardware profiles</strong>, then apply it to the ROM pane.</li>
              <li>Open <strong>ROM Workbench → Emulator</strong>. The panel identifies the managed tool and whether this machine has a proven ROM address mapping.</li>
              <li>If direct attachment is disabled, use Programmer export or place the ROM in a machine-specific image. The app does not guess a bank or replace a system ROM silently.</li>
            </ol></div>
            <div class="help-task"><h4>Troubleshoot a ROM</h4><ul>
              <li>If identity, processor or mapped addresses look wrong, confirm platform, layout and bank size before editing bytes.</li>
              <li>If modules are missing, check the machine's own module list on suitable hardware and inspect the initialisation routine. Static extraction intentionally rejects weak string-only matches.</li>
              <li>If disassembly looks meaningless, check architecture, origin and offset. The range may be text, tables, compressed data, an interleaved dump or unreachable code.</li>
              <li>If a programmed device fails, verify chip size, erased value, lane order, swaps, board links and read-back checksum against the Programmer report.</li>
              <li>Run <strong>Analyse → Image health dashboard</strong> after raw changes. Return to the checkpoint or untouched source when the result is uncertain.</li>
            </ul></div>
            <div class="help-warning"><strong>Hardware warning:</strong> a valid header does not prove that code is safe, correctly bank-switched or suitable for a particular machine. Make a checkpoint, retain the original dump and test an emulator or spare programmable device first.</div>
          </section>
          <section id="help-kickfs">
            <h3>Kickstart ROM data ROMs: complete workflow</h3>
            <p class="help-lead">A Kickstart ROM filing system is a genuine flat filing system stored in an Amiga ROM. It is shown as files, unlike a raw ROM's bank inventory.</p>
            <div class="help-task"><h4>Create a Kickstart ROM</h4><ol>
              <li>Choose <strong>File → New → New Image</strong>, then <strong>Amiga Kickstart ROM data ROM</strong>.</li>
              <li>Review the target platform. Amiga 500/1200 or Amiga 600 is preselected from the pane workbench profile when possible. If no profile applies, choose it in the dialog.</li>
              <li>Use 512 KiB for a Kickstart 2.0 or later ROM, 256 KiB for Kickstart 1.3, or 8 to 32 KiB for an expansion ROM. Enter a title of up to eight characters, the version byte and an Amiga copyright string beginning with <code>(C)</code>.</li>
              <li>Create the image, then use <strong>File → Insert File</strong>, folder import, drag and drop, or cross-pane Copy and Paste to populate it.</li>
              <li>Choose <strong>Tools → Check filesystem</strong>, save the timestamped ZIP, then test the ROM on an emulator or spare programmable device.</li>
            </ol></div>
            <div class="help-task"><h4>Edit and transfer files</h4><ol>
              <li>Double-click a BASIC, script, text or binary file to use the appropriate editor. The download arrow exports a loose copy with its load/execute metadata sidecar.</li>
              <li>Names are case-sensitive, contain up to ten Latin-1 characters and may include dots or slashes. Those characters are part of the name because Kickstart ROM has no directories.</li>
              <li>Use the pencil and × row controls to rename or delete. Multiple selections can be copied, exported or deleted together.</li>
              <li>In the Access column choose <strong>Make loadable</strong> or <strong>Mark *RUN-only</strong>. Kickstart ROM run-only protection is not the OFS/FFS lock bit.</li>
              <li>Host folders are flattened. Transfers to OFS or FFS apply that destination's shorter naming and hierarchy rules while retaining load and execution addresses where possible.</li>
            </ol></div>
            <div class="help-task"><h4>Identity, CRCs and safe editing</h4><ol>
              <li>Choose <strong>Tools → Kickstart ROM properties</strong> to edit the catalogue title, version byte and copyright. The ROM footer checksum is rebuilt.</li>
              <li>Every file header and data block carries a CRC. Normal edits rebuild the chain, and Check filesystem verifies it from the ROM header to the end marker.</li>
              <li>Complete plain Kickstart ROMs are rebuilt in storage order, so Compact is neither shown nor needed.</li>
              <li>A composite ROM with executable bytes after its catalogue, or an incomplete multi-ROM fragment, opens as a read-only safe view. Export its files instead of moving code and absolute pointers accidentally.</li>
              <li>The creator produces a selectable data ROM, commonly entered with <code>resident tag</code>. It does not claim to produce an autostart language ROM.</li>
            </ol></div>
          </section>
          <section id="help-hdf">
            <h3>Hard drives: partitions and the volumes they mount</h3>
            <p class="help-lead">An Amiga hard drive describes itself. Block 0 carries a Rigid Disk Block, which chains to one partition block for every partition, and each of those names the device the partition mounts as, its filing system and its size. Amiga File Forge reads that description and shows you the drive exactly as a machine would see it at boot.</p>
            <div class="help-task">
              <h4>Open a drive and browse a partition</h4>
              <ol>
                <li>Open the <code>.hdf</code> as you would any image. The pane shows the partition table: device name, filing system, size and whether the machine boots from it.</li>
                <li>Double-click a partition to open the volume inside it. From that point everything behaves as it does on a floppy: drawers open, files can be added, renamed, locked, dragged and downloaded.</li>
                <li>Choose <strong>View → Return to the partition table</strong> to come back out.</li>
                <li><strong>Tools → Compact filesystem</strong> and <strong>Check filesystem</strong> act on the open partition, not on the whole drive.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Create a new drive</h4>
              <ol>
                <li>Choose <strong>File → New → New Image</strong> and pick <strong>Partitioned drive · HDF with RDB</strong>.</li>
                <li>Enter a volume title and a capacity such as <code>20MB</code> or <code>512MB</code>.</li>
                <li>The new drive is created with a Rigid Disk Block and one FFS International partition, which is what an Amiga expects to find.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Bare hardfiles</h4>
              <ol>
                <li>A UAE hardfile has no partition table. Its geometry lives in a <code>.geo</code> sidecar instead, and both files must be opened together.</li>
                <li>Choose <strong>UAE hardfile · HDA + GEO sidecar</strong> when creating one. Surfaces, blocks per track and cylinders always multiply back to the exact file size.</li>
                <li>Opening a bare hardfile without its sidecar is refused rather than guessed at, because the wrong geometry silently reads the wrong blocks.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Convert between the two kinds</h4>
              <ol>
                <li>Choose <strong>File → Export as…</strong>. A drive with a Rigid Disk Block offers <strong>Bare hardfile and geometry sidecar</strong>; one without offers <strong>Partitioned drive with a Rigid Disk Block</strong>. Only the conversion that applies is listed, because converting a drive to the shape it already has is not a conversion.</li>
                <li>Adding a Rigid Disk Block copies the volume across unchanged and reserves one cylinder in front of it for the partition table, so the exported file is that much larger than the source. The drive then describes its own geometry, which is what lets <code>HDToolBox</code> and an emulator mount it without being configured first.</li>
                <li>Removing one exports the open partition as a bare <code>.hdf</code> with its geometry written beside it as a <code>.geo</code>, both inside a <code>Hardfile0</code> directory. The two files are only usable together: once the partition table is gone, the sidecar is the only place the geometry exists.</li>
                <li>The working image is not changed either way. The conversion downloads as a separate file, which you can open in another pane to check before using it.</li>
              </ol>
            </div>
            <div class="help-note"><strong>Both kinds use <code>.hdf</code>:</strong> that is what every Amiga emulator calls a hard-drive file. Which kind a file is comes from its contents - a Rigid Disk Block or the lack of one - and never from its name. The pane tells you which you have: a partitioned drive opens on its partition table, a bare hardfile opens straight into its files.</div>
          </section>
          <section id="help-online">
            <h3>Find and install software from the Online Library</h3>
            <figure><img src="/help/online-library.png" alt="Online Library showing machine, missing-title and multi-selection controls"><figcaption>Search several Amiga catalogues together, compare metadata and install one or many downloadable items.</figcaption></figure>
            <p class="help-lead">The Online Library uses the same format checks, metadata review and undo point as a file selected from your computer. A link is never treated as an installable image unless its source provides a direct supported download.</p>
            <div class="help-task"><h4>Install software into a drive or a floppy</h4><ol>
              <li>Open the destination: a partition on a hard drive, or a floppy image.</li>
              <li>Choose <strong>Library → Find software online</strong>. Its initial machine comes from the Workbench profile applied to this pane, or the remembered active Workbench profile when the pane has none. Change it when this search needs another machine, then search by title, publisher or keyword. Leave the search blank to browse the current catalogue page. Search results remain installable for one hour and survive a normal app restart.</li>
              <li>Select the <strong>Title</strong>, <strong>Publisher</strong>, <strong>Year</strong> or <strong>Source</strong> heading to sort. The active heading shows ↑ for ascending or ↓ for descending; select it again to reverse the order. Checked results stay selected while sorting.</li>
              <li>Use <strong>Not already present</strong> to hide likely matches found by disk title, or a remembered online distribution name. The comparison ignores punctuation and the publisher suffix saved with online imports. This is a helpful duplicate check, not a checksum guarantee.</li>
              <li>The initial results contain only records whose supported media has been verified. Large indexes are checked in bounded groups. Choose <strong>Find more downloadable results</strong> until the status says every matching catalogue entry has been checked. Existing results and selections are retained. Shared <strong>Amiga/Amiga 600</strong> releases are included in both machine families.</li>
              <li>Select several downloadable results. Each one's expanded size is measured against the destination's free space before anything is written, so a batch that will not fit is reported before its first write rather than part way through.</li>
              <li>Review the title, publisher, launcher and action detected for each item after insertion, together with the stack its launcher needs. Every proposal carries the evidence behind it, and an ambiguous one is marked rather than written silently.</li>
              <li>During a multi-item install, <strong>Abort operation</strong> stops before the next download. The item already in progress finishes at a safe image boundary. The foreground status reports elapsed time, measured item throughput and an ETA once enough completed work exists to calculate them honestly.</li>
              <li>If an archive contains the same release as both ADF and DMS, the native ADF is selected once. Installing into a blank ADF adopts its catalogue and title; shortened ADF files are safely padded to the target's standard geometry.</li>
            </ol></div>
            <div class="help-task"><h4>Insert files or applications into an open disk</h4><ol>
              <li>Open an ADF/ADZ disk, a hard-drive partition, an FFS drawer, or an AmigaOS image and choose <strong>Library → Find software online</strong>.</li>
              <li>On OFS, ordinary single-catalogue downloads are copied into the currently open group. Multi-prefix distributions retain their original OFS prefixes so loaders and duplicate leaf names remain valid.</li>
              <li>On FFS, a downloaded disk is extracted into the current directory by default. Select <strong>Create a folder</strong> to keep each disk separate. The preflight allocates distinct legal names across the complete batch and existing destination entries, adding numeric suffixes when ten-character truncation would otherwise create a clash.</li>
              <li>AmigaOS packages install only into FFS/AmigaOS images. Application drawers are retained, package-control files are omitted, and the protection bits an Amiga-made ZIP records are preserved.</li>
            </ol></div>
            <h4>Sources, availability and safety</h4><ul>
              <li>Built-in sources are Aminet, the WHDLoad installer index, Hall of Light, Lemon Amiga, cautious itch.io Amiga searches and OS4Depot. Every Game Going ships disabled until its Amiga machine identifiers are confirmed.</li>
              <li>Aminet's game, demo, disk and utility trees are indexed, along with the WHDLoad installer index. Records without a supported public download are omitted.</li>
              <li>Every Game Going needs its Amiga machine identifiers configured before it can be enabled, which is why it ships disabled. Each matching item page is checked for actual downloadable media before it is displayed, and continuation checks expose the whole matching index without opening thousands of remote pages at once.</li>
              <li>A blocking compatibility report provides <strong>Change selection or import options</strong>, which returns focus to the relevant controls. The final Install action remains unavailable until a fresh report can proceed.</li>
              <li>itch.io uses the selected workbench machine to search for Amiga 500, Amiga 1200, Amiga 600, Amiga 4000 or AmigaOS software. Unrelated amiga-themed games are suppressed: a project is displayed only after its page is found to contain a supported Amiga disk or dms upload. A fresh short-lived download is requested when Install is selected.</li>
              <li>Choose <strong>Sources…</strong> to edit a provider's URL, loading strategy, page layout, category roots, query templates, machine IDs, validation limit and cache settings. The engine applies generic configured stages and never branches on a catalogue name. The editable JSON is stored in <code>catalog-sources.json</code>.</li>
              <li>Downloads are size-limited, cached briefly and checked for ZIP path traversal. A failed source is reported below the usable results instead of cancelling the complete search.</li>
            </ul>
            <div class="help-warning"><strong>Respect each archive and author:</strong> availability in a catalogue does not change a program's licence. Follow the source page for permissions, payment, documentation and the newest release.</div>
          </section>
          <section id="help-ffs">
            <h3>FFS, Amiga 4000 and AmigaOS images</h3>
            <div class="help-task">
              <h4>Create and organise an FFS volume</h4>
              <ol>
                <li>Create an OFS or FFS floppy in any writable DOS type, an RDB or RAW hard-drive image, or open a supported existing image.</li>
                <li>Double-click directories to enter them. Double-click <strong>..</strong> or use the breadcrumbs to move back through the hierarchy.</li>
                <li>Use <strong>File → New → New folder</strong> to create a validated drawer at the current location.</li>
                <li>Use <strong>File → New → New file</strong> for an empty, correctly named file with explicit protection bits and an optional comment. This is also available inside any writable drawer.</li>
                <li>Use <strong>File → Insert File</strong> to import host files with their protection bits, comment and optional Workbench icon type.</li>
                <li>When the selected host file is a recognised disk, DMS or ZIP image, review its catalogue preview before anything is written.</li>
                <li>Extraction defaults to the directory currently shown. Optionally choose another existing destination with the directory picker, and optionally create a named child directory there. You can instead store the original image as an ordinary file.</li>
                <li>Direct extraction never overwrites an existing name. A rollback point protects the complete working image if extraction fails or is aborted.</li>
                <li>Use the pencil and × icons on each row to rename or delete. Use the Access-column actions to mark one or several items read/write or read-only.</li>
                <li>Drag files and complete directory trees onto another directory in the same image to reorganise them.</li>
                <li>Check and compact the working filesystem, then save the image.</li>
              </ol>
            </div>
            <div class="help-note"><strong>Choose the target hardware deliberately:</strong> Auto inspects without applying machine-specific checks. Amiga 500 / 2000 restricts a volume to what Kickstart 1.3 can mount, which is OFS only. Amiga 600 / 1200 allows every DOS type Kickstart 2.0 and later understands. Hardfile requires a matched HDA and GEO pair. AmigaOS hard drive assumes a Rigid Disk Block and a machine that can load a file system from it.</div>
            <div class="help-task">
              <h4>Import a complete disk or dms into a directory</h4>
              <figure><img src="/help/image-import-preview.png" alt="Image import dialog previewing Chuckulus files with optional destination and child-directory controls"><figcaption>Inspect the source before writing. Direct extraction into the current directory is the default; destination browsing and a new child directory are independent options.</figcaption></figure>
              <ol>
                <li>Navigate to the FFS directory that will contain the imported software.</li>
                <li>Drag an ADF/ADZ/HFE/SCP image, DMS archive or another supported image from another pane; alternatively use <strong>File → Insert File</strong> and select an image from the host.</li>
                <li>Review the source preview. The current directory is selected by default; optionally tick <strong>Choose a different existing directory</strong> and browse the destination tree.</li>
                <li>Optionally tick <strong>Create a new child directory</strong> and enter its name. Leave it unticked to place the source contents directly in the selected destination.</li>
                <li>A floppy is not necessarily relocatable. The importer follows the loader stages a boot script actually reaches and makes proven <code>DF0:</code> references relative to the script&rsquo;s own drawer. It warns when a reachable loader switches filing system or drive, or appears to use direct sector I/O. Those titles should remain mounted as floppy images unless a specific HDD installer exists.</li>
                <li>Review progress and metadata. During a bulk copy, an empty disk pauses for a Skip or Abort decision; no meaningless empty drawer is created.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Check software already installed on an HDD</h4>
              <ol>
                <li>Open the hard-drive partition and choose <strong>Tools → Check installed disk software</strong>. This command is intentionally unavailable on floppy images.</li>
                <li>Choose the whole HDD or the current directory. The read-only pass recursively finds imports from retained source-image details and conventional launch files including <code>Startup-Sequence</code>, <code>LOADER</code>, <code>MENU</code>, <code>GO</code> and <code>START</code>.</li>
                <li>Review every directory. The result shows the source image when known, its file count, every exact proposed rewrite and warnings which require human testing.</li>
                <li>Drawer paths are resolved against the installed tree before commands are classified. A real path such as <code>Data/Levels</code> is preserved rather than mistaken for a device reference.</li>
                <li>Changed AmigaBASIC lines receive corrected line-length bytes, and the audit can repair malformed lengths left by older imports before continuing its loader analysis.</li>
                <li>Select the deterministic repairs to apply and choose <strong>Repair selected</strong>, or choose <strong>Cancel</strong> to leave the image untouched. An automatic undo checkpoint is made before a repair.</li>
                <li>Run the check again. Proven current-directory path and loader-command issues should be clear. Explicit filing-system changes and direct-sector I/O remain warnings because automatically changing those behaviours would be unsafe.</li>
                <li>Older sessions may contain loader diagnoses made before the current path-aware audit. Those point-in-time messages are replaced by one review notice, repeated directory and accelerator notices are consolidated, and actual byte-level compatibility changes remain in the saved history.</li>
              </ol>
            </div>
            <p>Where both formats support it, Amiga File Forge preserves protection bits, file comments, Workbench icon types and datestamps. An AmigaDOS name is limited to 30 characters.</p>
            <div class="help-note"><strong>Very large imports:</strong> an AmigaDOS drawer hashes its entries into a 72-entry table and chains the collisions, so there is no fixed limit on how many files a drawer holds; what runs out first is the volume&rsquo;s free blocks. A directory-cache DOS type additionally maintains cache blocks, which the planner rebuilds as the drawer grows. A large selection is divided into parent groups only when required, and names such as <code>DISCS1</code> and <code>DISCS2</code> remain editable suggestions.</div>
          </section>
          <section id="help-hardfile">
            <h3>Hardfile HDA and GEO: open, edit and save</h3>
            <ol>
              <li>Select either the HDA data file or its matching GEO descriptor.</li>
              <li>Choose <strong>Hardfile HDA + GEO</strong>. This is separate from the normal FFS machine profiles because Hardfile is available for Amiga 600, Amiga 500 and 1200 hosts.</li>
              <li>In the pairing dialog, the chosen file is already retained. Select only the missing companion.</li>
              <li>Confirm that both base names match, for example <code>SCSI0.hda</code> and <code>SCSI0.geo</code>, then select <strong>Open HDA + GEO</strong>.</li>
              <li>Traverse, create, add, rename, move, lock and delete content using the normal FFS controls.</li>
              <li>Select <strong>Save Image</strong> in the pane heading. The same foreground progress dialog used by every format reports validation, checksums, catalogue generation, elapsed time, throughput, ETA and construction of the complete ZIP. For HDA it also names geometry, directory and map checks. The ready dialog appears only after the hardware-ready ZIP containing <code>Hardfile0/scsi0.hda</code> and <code>Hardfile0/scsi0.geo</code> is complete on disk. If the automatic download does not begin, use the direct <strong>Download ZIP</strong> link.</li>
              <li>Extract the ZIP into the root of the Hardfile SD card. Keep the <code>Hardfile0</code> directory itself. The firmware does not look for HDA/GEO files directly in the SD-card root.</li>
            </ol>
            <div class="help-note"><strong>Large-image performance:</strong> once an FFS image has been identified, directory changes use a direct memory-mapped view and return the catalogue and free-space value together. The app does not copy or re-identify the complete HDA for every click. Imports keep one destination mount open for the batch. Zero-filled free HDA capacity is also kept sparse in the working image and undo checkpoints, while downloads use fast ZIP compression and sparse-aware checksumming. The extracted HDA retains its complete logical size and exact bytes.</div>
            <div class="help-note"><strong>Why the target matters:</strong> every AmigaDOS block carries a checksum that sums its longs to zero, and a directory that fails it is reported by <code>DiskDoctor</code> as damaged. An edited volume must also carry a later datestamp, or a machine that already mounted it can serve a stale cached view and report a broken directory. The Hardfile target performs those checks, advances the disc ID and rebuilds its map checksum before download.</div>
            <div class="help-warning"><strong>Do not substitute a descriptor:</strong> GEO geometry belongs to its particular HDA. An HDA without valid matching geometry may be browsed when identifiable, but writing is deliberately blocked to prevent corruption. The HDA ends at the old-format FFS map boundary, as in the official Hardfile Quickstart image; the GEO may describe a slightly larger device. Newly created pairs are checked against that map extent and Hardfile's 256-byte sector, 33-sector track, 16-head and FFS 21-bit size limits before download.</div>
          </section>
          <section id="help-DMS archives">
            <h3>DMS archives: inspect, export and convert</h3>
            <div class="help-task">
              <h4>Convert a DMS archive back to a disk</h4>
              <ol>
                <li>Open the DMS in any pane. Its tracks are listed with their compression modes and both CRCs; a DMS holds a whole floppy, not a directory.</li>
                <li>Choose <strong>Tools → Convert archive to disk</strong>.</li>
                <li>Select ADF or ADZ as the destination format.</li>
                <li>Every track is written back at the cylinder it came from, so the result is the disk the archive was made from. A track the archive omits, which DiskMasher does for an empty one, is left as zeroes.</li>
                <li>A track this build cannot decompress stops the conversion and says which, rather than producing a disk with a hole in it.</li>
                <li>Choose which other pane receives the rebuilt disk, then browse it like any other volume.</li>
              </ol>
            </div>
            <p>Double-click an individual DMS track to open its hex view, or use the download arrow beside its name to export the raw cylinder. A track stored uncompressed can be replaced when its length does not change; save first opens a structural review listing every changed and preserved track. The rebuild changes only the selected payload and its CRCs, and every other track retains its exact bytes. A track that is incomplete, compressed, or whose replacement changes length stays read-only. <strong>Tools → DMS archive project</strong> shows the full track inventory and the reason each one is writable or protected. A DMS stored inside another filing system follows the same rules: detection uses the content, so a file named <code>Games/Thrust</code> opens as a DMS without a <code>.dms</code> suffix, and a gzip-compressed archive works too. Drag the archive onto a hard-drive pane to rebuild the disk and copy its files into a new drawer.</p>
          </section>
          <section id="help-transfer">
            <h3>Copy and drag between panes</h3>
            <figure><img src="/help/workspace.png" alt="Amiga images open together for drag and drop"><figcaption>Navigate the destination first, select one or more source items, then drag any selected row into another pane.</figcaption></figure>
            <div class="help-task">
              <h4>Cut, copy and paste</h4>
              <ol>
                <li>Select one or several source rows, then choose <strong>Edit → Cut</strong> or <strong>Edit → Copy</strong>. Ctrl/Cmd-X and Ctrl/Cmd-C do the same while the pane has focus.</li>
                <li>Navigate normally to the destination. Opening drawers, partitions and other panes does not lose the pending selection.</li>
                <li>Choose <strong>Edit → Paste</strong>, or press Ctrl/Cmd-V in the destination pane. The same filename, capacity and filesystem checks used by drag and drop are applied.</li>
                <li>The clipboard is single-use. Paste, cancelling a paste, pressing Escape, or starting a different modifying operation clears it. A cut is not removed from its source until its destination has been written successfully.</li>
                <li>When files are pasted between volumes, review the proposed names against the 30-character AmigaDOS limit. Drawers can be pasted into OFS as well as FFS, because the two DOS types differ in how they store a file&rsquo;s data, not in how they store a directory.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Copy files or directories</h4>
              <ol>
                <li>Open the source in one pane and a writable destination in another.</li>
                <li>Navigate the destination to the exact drawer required.</li>
                <li>Select one or more source files. Complete FFS directories can also be selected for an FFS destination.</li>
                <li>Drag any selected row into the destination pane.</li>
                <li>Review replacement filenames where the target has stricter naming rules, then confirm the copy.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Move items inside one FFS image</h4>
              <ol>
                <li>Select one or more files or directories in an FFS pane.</li>
                <li>Drag any selected row onto a destination directory row, or into another pane showing a different directory in the same image.</li>
                <li>The operation moves rather than copies. Existing destination objects are never silently replaced.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Copy several floppies onto a hard drive</h4>
              <ol>
                <li>Open the destination partition and navigate to the drawer that will hold the software.</li>
                <li>Select one or more floppy images in another pane, or use <strong>File → Insert File</strong> and select several from the host.</li>
                <li>Each non-empty disk becomes a drawer named from its volume title. Review and edit the parent group drawers; names such as DISCS1 are suggestions, not fixed names.</li>
                <li>If shortened names would clash, keep the default unique DISC-0000 naming scheme or review the highlighted names manually.</li>
                <li>The preflight keeps naming and parent groups on the left. Review or edit the dense disk-to-drawer table on the right; its rows scroll without moving the Copy button.</li>
                <li>If a formatted but empty disk is found, choose <strong>Skip this disk and continue</strong> or <strong>Abort bulk copy</strong>. The dialog names the image and its volume title.</li>
                <li>Watch the foreground progress dialog. If interrupted, use the retry path to skip items already completed in that dialog.</li>
              </ol>
            </div>
            <figure><img src="/help/copy-name-preflight.png" alt="Bulk copy preflight offering generic DISC-0000 names or manual review"><figcaption>The naming choice appears only when the complete preflight finds names that would clash after shortening. Generic names are selected by default.</figcaption></figure>
            <div class="help-task">
              <h4>Resolve shortened-name collisions before copying</h4>
              <ol>
                <li>The preflight checks every proposed leaf name case-insensitively within its destination parent.</li>
                <li>If there is no collision, the normal safe names are retained and no naming-strategy choice is shown.</li>
                <li>If shortening or sanitising creates a collision, choose <strong>Use generic unique names</strong> for <code>DISC-0000</code>, <code>DISC-0001</code> and so on.</li>
                <li>Alternatively choose <strong>Review shortened names</strong>. Conflicting inputs are highlighted and the copy cannot start until every name is legal and unique in its parent.</li>
                <li>Generic names make the outer disk drawers unique. Each disk keeps its own drawer, so two disks that share a filename cannot collide during extraction.</li>
              </ol>
            </div>
            <figure><img src="/help/destination-conflict.png" alt="Populated FFS destination conflict with Abort, Keep existing and Replace choices"><figcaption>An existing empty directory is filled automatically. These choices appear only when the existing destination contains files or directories.</figcaption></figure>
            <div class="help-task">
              <h4>When a destination already exists</h4>
              <ol>
                <li>If the existing destination is a directory with no children, it is reused automatically without interrupting the batch.</li>
                <li>If it is populated, choose <strong>Keep existing and continue</strong> to leave it untouched and skip that source disk.</li>
                <li>Choose <strong>Replace and continue</strong> to remove the populated directory recursively, recopy the current disk, and continue.</li>
                <li>Choose <strong>Abort bulk copy</strong> to preserve completed work and start no further disks.</li>
                <li>A same-named file is never treated as an empty directory and is never overwritten silently.</li>
              </ol>
            </div>
            <h4>Transfer behaviour at a glance</h4>
            <div class="help-table-wrap"><table class="help-table"><caption class="visually-hidden">Results of transferring supported source types between image formats</caption>
              <thead><tr><th>Source</th><th>Destination</th><th>Result</th></tr></thead>
              <tbody>
                <tr><td>File</td><td>OFS or FFS</td><td>Copied with compatible metadata</td></tr>
                <tr><td>FFS directory</td><td>FFS</td><td>Recursive directory copy</td></tr>
                <tr><td>ADF/ADZ/HFE/SCP, DMS or IPF</td><td>Hard-drive partition</td><td>Extracted into a new drawer; ambiguous loader commands are checked</td></tr>
                <tr><td>Several floppy images</td><td>Hard-drive partition</td><td>One drawer per non-empty disk, grouped if necessary; every disk is checked</td></tr>
                <tr><td>Drawer or file</td><td>Floppy image</td><td>Copied when the volume has the free blocks for it</td></tr>
              </tbody>
            </table></div>
            <div class="help-task">
              <h4>Convert floppy-bound scripts safely for a hard drive</h4>
              <p>A script written for a floppy names its files through the drive they came in, as <code>DF0:Game</code>. That reference is correct in a drive and wrong the moment the software is copied to a hard drive, because <code>DF0:</code> is then empty or holds a different disk.</p>
              <ol>
                <li>Import a DMS, ADF, ADZ, HFE or SCP image into a drawer on a hard drive in the usual way.</li>
                <li>Readable <code>Startup-Sequence</code>, <code>DiskMenu</code>, <code>Menu</code>, <code>Loader</code> and <code>Start</code> scripts have their <code>DF0:</code> to <code>DF3:</code> references rewritten as paths relative to the script's own drawer.</li>
                <li>Amiga File Forge starts with the boot script, follows the launch target it names, and checks only those reachable scripts. Unrelated documentation, reviews and game data are not treated as loaders.</li>
                <li>A reference is rewritten only when exactly one file in the volume carries that name. Anything ambiguous is left alone and reported, because a wrong repair is worse than none.</li>
                <li>The replacement is padded with spaces to the length it replaced, so the file's size and every offset after it are unchanged.</li>
                <li>A replacement longer than the reference it replaces is refused, because lengthening the file would move every block after it.</li>
                <li>A persistent image warning names the source image or drawer, affected file, old command and replacement. For example: <code>FastFileSystem compatibility change: Startup-Sequence: DF0:Game &rarr; Games/Game</code>.</li>
                <li>If a reference cannot be resolved to exactly one file, no bytes are changed. Unresolved references from the same script are condensed into one warning for manual testing.</li>
                <li>Test the imported program on its intended hardware before saving the final image. A static check cannot prove every self-modifying, protected or dynamically constructed loader.</li>
              </ol>
              <div class="help-warning"><strong>Existing imports are not silently rewritten:</strong> compatibility analysis runs while files are copied into FFS. To repair a directory imported with an older version, delete that directory and import its DMS, ADF, ADZ, HFE or SCP again. If the existing directory is populated, choose Replace only after confirming it is the correct target.</div>
            </div>
          </section>
          <section id="help-maintenance">
            <h3>Check, compact and monitor operations</h3>
            <div class="help-task">
              <h4>Check a filesystem</h4>
              <ol>
                <li>Open the OFS or FFS filesystem you want to inspect. On a hard drive, open the partition first.</li>
                <li>Choose <strong>Tools → Check filesystem</strong>.</li>
                <li>Wait for the result. A structural error is reported without changing the working image.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Compact a filesystem</h4>
              <ol>
                <li>Create a named checkpoint first if the current working state is important.</li>
                <li>Choose <strong>Tools → Compact filesystem</strong>.</li>
                <li>Optionally list paths that should be placed first, such as <code>S/Startup-Sequence,C/LoadWB</code>.</li>
                <li>Confirm. Files are reorganised into low contiguous sectors and free space is consolidated.</li>
                <li>Run Check filesystem afterward, then save the compacted image.</li>
              </ol>
            </div>
            <h4>Progress, abort and retry</h4>
            <ul>
              <li>Creative and destructive controls disable as soon as an operation starts, preventing duplicate clicks.</li>
              <li>The foreground dialog reports the current phase, disk or file and completed count. Error details appear in the same foreground dialog.</li>
              <li><strong>Abort operation</strong> requests a stop at the next safe boundary. The current low-level filesystem write may need to finish first.</li>
              <li>Completed items in a bulk-copy dialog remain recorded. Use its retry path to continue with the remaining items.</li>
              <li>Do not close the browser or container during a write. A normal page refresh keeps active server sessions, but the pane should be refreshed before retrying an interrupted action.</li>
            </ul>
          </section>
          <section id="help-hex-editor">
            <h3>Raw image hex editor</h3>
            <p class="help-lead">Use the raw editor for deliberate low-level repairs and experiments. It works over the current pane without loading a complete HDD image into the browser.</p>
            <figure><img src="/help/hex-editor.png" alt="Raw image hex editor showing offset, byte, ASCII and value views"><figcaption>The editor overlays only its source pane. Other panes remain visible for reference, while the selected image is protected from other pane actions until the editor closes.</figcaption></figure>
            <div class="help-warning"><strong>Important:</strong> raw edits bypass OFS, FFS, RDB, DMS and container rules. A plausible-looking byte change can destroy a catalogue, free-space map, checksum or disk geometry. Create a named checkpoint first when the current state matters.</div>
            <div class="help-task"><h4>Inspect and navigate raw bytes</h4><ol>
              <li>Open <strong>Tools → Hex editor</strong> in the relevant pane. It is available at a drive&rsquo;s partition table as well as inside normal filesystem views.</li>
              <li>For a paired Hardfile image, choose the HDA or GEO from <strong>Component</strong>. The GEO option edits only the 22-byte geometry descriptor.</li>
              <li>Use first, previous, next and last page, or enter a hexadecimal offset in <strong>Go to offset</strong>. Append <code>d</code> to enter a decimal address.</li>
              <li>Choose a 128, 256, 512 or 1,024-byte page. Only that range is fetched, even for a multi-gigabyte image.</li>
              <li>Select a hex or ASCII cell. The inspector shows unsigned 8, 16 and 32-bit values in little and big-endian order.</li>
              <li>Open <strong>Analyse</strong> to compare the current bytes with a local binary. Differing bytes are marked in the grid, the inspector reports byte and size differences, and <strong>Next difference</strong> navigates through them.</li>
              <li>Select a structure template to decode generic values, an AmigaDOS boot or root block, a Rigid Disk Block, a Kickstart ROM header, a resident module tag, a hardfile GEO descriptor or a DMS header and track. Automatic mode recognises safe signatures; a template is an interpretation only and never changes bytes.</li>
            </ol></div>
            <div class="help-task"><h4>Search, select and edit</h4><ol>
              <li>Search for hexadecimal byte pairs such as <code>44 69 73 63</code>, or switch the search to Latin-1 text. Find previous and Find next can wrap around the image. Find and Replace selects the complete matched range and stages a same-length replacement; it cannot insert or remove raw bytes.</li>
              <li>Click a byte, Shift-click another byte, or hold Shift while using the arrow keys to select a range.</li>
              <li>Choose HEX or ASCII mode, then type to replace bytes. You can also paste, fill the selection with one byte, copy as hex or text, or revert selected edits.</li>
              <li>Undo and redo affect staged editor changes only. The staged-change list shows the original and replacement value at every changed offset and can jump back to it.</li>
              <li>Use Ctrl/Cmd-S to write, Ctrl/Cmd-Z or Ctrl/Cmd-Y for undo or redo, Ctrl/Cmd-F to search, Ctrl/Cmd-H for replacement, Ctrl/Cmd-G to go to an offset, and Escape to close.</li>
            </ol></div>
            <div class="help-task"><h4>Write or close safely</h4><ol>
              <li>Select <strong>Write changes</strong>. Read the <strong>This is dangerous. Are you sure?</strong> warning and confirm only if the listed byte count is expected.</li>
              <li>The server rejects the write if another action changed the image after the editor loaded it. It also rejects overlaps, out-of-range writes, resizing and an unconfirmed request.</li>
              <li>An automatic undo checkpoint is created before the fixed-size byte ranges are flushed. Cached partition, directory, dms and export data is cleared so later views cannot reuse stale content.</li>
              <li>Closing with staged bytes offers Keep editing, Discard changes, or Review and write. A protected advanced HFE can be inspected but not written.</li>
              <li>Refresh the pane and run <strong>Analyse → Image health dashboard</strong> after every raw write. Use Edit → Undo last change if the result is not sound.</li>
            </ol></div>
          </section>
          <section id="help-analysis">
            <h3>Workbench, analysis and repeatable workflows</h3>
            <p class="help-lead">The Analyse menu in each pane checks the image in context. Workbench in the page header stores reusable settings and portable workspace descriptions.</p>
            <div class="help-task"><h4>Run a complete image health check</h4><ol>
              <li>Open the pane's <strong>Analyse</strong> menu and choose <strong>Image health dashboard</strong>.</li>
              <li>Read the duration warning. Large hard-drive images may take several minutes. The progress view names the current partition and directory and reports elapsed time, throughput and ETA. Abort operation stops at a safe boundary.</li>
              <li>If the host or client is interrupted, reopen the same installation under the same web profile or Linux user. Recover previous session restores owner-isolated working images, while History marks an in-flight server job as interrupted instead of pretending that it completed.</li>
              <li>Review filesystem, geometry, Rigid Disk Block, launcher, STACK, compatibility and hardware-profile findings together. A failed check expands into the individual records behind it, each showing the volume, path, launch command, STACK, exact problem and supporting evidence.</li>
              <li>If a provably safe STACK repair is available, inspect the itemised count and choose <strong>Repair stack sizes</strong>. An automatic checkpoint is made first.</li>
              <li>Run the dashboard again after repairs. A failed launcher check remains manual, because inventing a target would be unsafe.</li>
            </ol></div>
            <figure><img src="/help/health-dashboard.png" alt="Image health dashboard with an expanded failed launcher record"><figcaption>Each failed check includes actionable evidence. Expand it to see the volume, path, launch command, STACK and exact problem.</figcaption></figure>
            <div class="help-task"><h4>Dry-run a change</h4><ol>
              <li>Select one or more files or directories.</li>
              <li>Choose <strong>Analyse → Dry-run selected items</strong>.</li>
              <li>Review target-name conversion, truncation and case-insensitive clashes. The dry run does not write the image.</li>
              <li>Bulk HDF-to-FFS imports perform their more detailed capacity, grouping and collision plan in the copy dialog.</li>
            </ol></div>
            <div class="help-task"><h4>Inspect a file or loader</h4><ol>
              <li>Double-click a file in any filesystem pane, or select it and choose <strong>Analyse → Open selected file</strong>. Use the download arrow beside its name when you only want the original file and metadata.</li>
              <li>Open one tokenised AmigaBASIC or machine-code file and choose <strong>Tools → Find cheat candidates</strong> inside its editor for a read-only report. On a wide editor the report docks on the right at full listing height and scrolls independently; narrow windows place it below the code. Drag the separator, or focus it and use the arrow keys, to resize the two sections. Select a candidate to centre and highlight its BASIC line or decoded address. BASIC requires corroboration between semantic state, plausible initialisation, updates, terminal tests and gameplay outcomes. Machine code joins initialisation, access to the same storage, updates, forward terminal branches and saved labels across 68000, 68010, 68020, ARM and 68000. Reachable unlabelled state changes with a forward decision are retained as Possible; speculative instructions decoded from embedded data are excluded. Unexplained writes, opaque countdowns, backward decrement loops, hardware registers and likely copy, clear, scan or delay counters are suppressed. Loader commands and packed or runtime-generated payloads are identified explicitly when static analysis cannot reach the final game code. Trainer-style NOP, RTS, JMP and BIT writes remain separately identified with a warning. Filter by likely purpose or confidence. Online title evidence and configured specialist searches are optional and never prove that similarly named bytes match. For a selected machine-code result with an exact offset, <strong>Prepare guarded patch</strong> records the complete source hash, guarded bytes, hardware profile, two tester-supplied emulator observations, rationale, author and rollback instructions. Apply verifies the hash and bytes again and creates an automatic checkpoint. The host-private library matches exact files, never titles. Automatic watchpoint correlation is not claimed.</li>
              <li>Tokenised AmigaBASIC 1.0 opens as numbered editable source with a space after every line number. Use <strong>Tools → Renumber BASIC</strong> to update line numbers and encoded GOTO, GOSUB and other references without changing numbers inside strings.</li>
              <li>When pasting into BASIC, choose whether to validate and normalise numbered AmigaBASIC source or insert the clipboard exactly as plain text. The complete listing must be valid BASIC before Save can retokenise it.</li>
              <li><code>Startup-Sequence</code>, <code>Loader</code> and other recognised scripts open as compact unnumbered script editors. Edit their ordered AmigaDOS command lines directly.</li>
              <li>Source and disassembly windows open centred at a useful desktop working size, then scale proportionally on smaller browser windows. They can be moved by dragging the title bar and resized from any edge or corner. Use the square title-bar control, or double-click the title bar, to maximise and restore the editor. The window remains constrained to the visible browser area and resizing does not disturb the document or its scroll position. File and Edit menus provide Save, Save As, Export, Close, undo, redo, clipboard actions, Select All, Find and Find and Replace. Replace Next starts at the current selection and wraps once; Replace All reports how many case-insensitive matches it changed. Save As creates a sibling inside the image while Export downloads readable source as browser-local text. Read-only disassembly retains Find without unsafe source replacement.</li>
              <li>The tab strip keeps several files from the mounted image open together. It retains each source draft, selection and scroll position, marks dirty tabs and warns before discarding one. <strong>Open from image…</strong> searches filenames and bounded readable content, restores the result's partition and directory, and opens it as another tab.</li>
              <li>BASIC and command scripts use themed syntax colours for keywords, strings, numbers, comments, symbols and line numbers. The normal textarea remains the editable document, preserving browser undo, clipboard and input-method behaviour. Hover a highlighted command for its purpose, syntax, requirements and important compatibility notes. One catalogue covers AmigaBASIC 1.0 plus the extra statements AmigaBASIC 1.2 added, with availability checked against the detected dialect. A word glued to a name, such as <code>printer</code> or <code>total</code>, stays a variable, because that is what the interpreter's own tokeniser does with it.</li>
              <li>An AmigaDOS script and an AmigaBASIC program are told apart by vocabulary, because AmigaDOS names its commands without any sigil. <code>LOAD "Program"</code> in a BASIC line shows AmigaBASIC LOAD help, while <code>Execute Loader</code> in a script shows the AmigaDOS command's syntax. RUN and other overlapping names follow the same rule.</li>
              <li>Help interprets command names and constant operands. <code>POKEW &amp;HDFF180,0</code> names <code>COLOR00</code> and says the write goes straight to the hardware; an odd address for a word or long write is reported, because a 68000 cannot make one. <code>SCREEN</code> explains what a depth costs in Chip RAM and warns when a mode interlaces. <code>LIBRARY</code> names the <code>.bmap</code> file the call needs. SOUND, WAVE, SAY and PALETTE show every proven argument. The result is compared with the hardware profile applied to the pane, and a call documented for a different chipset is explained and then marked as out of scope. Dynamic expressions remain unguessed.</li>
              <li>Assembly source decodes proven constant calls. A <code>JSR</code> through a negative offset from A6 is named as the library function it calls, using the immediate loads earlier on the same line for its parameters. <code>MOVEA.L $4,A6</code> is recognised as reading ExecBase, and an absolute operand that names a custom-chip register is labelled.</li>
              <li>Hover a 68000 mnemonic, a library vector symbol such as <code>_LVOOpenLibrary</code>, an absolute address such as <code>$DFF180</code>, or a directive such as <code>DC.B</code>. The processor catalogue keeps the 68000, 68010, 68020, 68030, 68040 and 68060 instruction sets distinct rather than treating every extension as interchangeable, so an instruction a machine cannot run is not offered as if it could.</li>
              <li>Press <strong>F1</strong> for help on the command at the caret. The editor's <strong>Help</strong> menu gives an overview of the detected language, a searchable command reference, live problems and document symbols. Problem and symbol entries jump back to their source location.</li>
              <li><strong>Edit → Find all references</strong> lists code uses of the symbol at the caret. <strong>Rename symbol</strong> changes those uses as one undoable operation while leaving strings and comments alone. The BASIC program outline lists subprograms and functions with their call sites. Diagnostics also flag unused definitions, mismatched SUB endings and conservative unreachable-line candidates.</li>
              <li><strong>Find and Replace</strong> stays open while you work and supports match case, whole identifiers, regular expressions, selection-only scope, previous/next, one replacement, preview and Replace All. <strong>Search files in this image</strong> finds names and bounded readable content across the mounted filesystem, then opens the containing location. <strong>Analyse file dependencies</strong> checks the entire image and reports exact, unique, ambiguous, missing and root-relative launcher targets.</li>
              <li>Press <strong>Ctrl+Space</strong> for completions from known commands, identifiers, document symbols and templates. Text and script files provide duplicate, move, join and delete line operations. BASIC disables line moves that cannot preserve line-number meaning. <strong>Format selection or file</strong> applies conservative whitespace rules; BASIC must pass a token round trip before the proposal is applied.</li>
              <li>Refactor and Condense show the original and proposed source side by side. Changed rows are marked. Every BASIC proposal completes an exact tokenise, detokenise and retokenise check before acceptance; the review displays its line count and tokenised byte size. Use <strong>Tools → Verify BASIC round trip</strong> to run the check independently, and <strong>Editor history</strong> to review accepted transformations and symbol renames from this window.</li>
              <li><strong>View → Show synchronized bytes</strong> follows the BASIC line, text caret or selected disassembly row. It shows the matching saved bytes and printable characters, with a shortcut into the full Hex editor. Unsaved source is never presented as if it had already changed the image. A new or renumbered BASIC line reports that it has no saved byte range until Save succeeds.</li>
              <li>Live BASIC checks cover missing, duplicate and out-of-order line numbers, unresolved direct GOTO, GOSUB and RESTORE destinations, a CALL with no matching SUB, a SUB with no END SUB, and unclosed strings. Script checks cover unclosed strings and floppy-device references that will not resolve once the software is on a hard drive. Treat these as focused editing checks rather than proof that software will run on every target.</li>
              <li>Use <strong>Edit → Go to line</strong> for a physical source line or BASIC line number. BASIC selections can be commented or uncommented with <strong>Toggle comment</strong>. <strong>Tools → Normalise recognised commands</strong> follows the detected language convention while leaving strings, comments and identifiers unchanged. AmigaBASIC normalises its keywords to uppercase, and AmigaDOS scripts to the mixed case the commands are named in; the mechanism can support lowercase-preferring languages.</li>
              <li><strong>Tools → Refactor selection or program</strong> applies to one selected line, a selected block, or the complete program when nothing is selected. It opens a non-destructive proposal that normalises proven BASIC commands, expands every safe colon-separated operation, and turns nested IF/ELSE IF/ELSE forms into readable guarded branches without changing their scope. It renumbers from 10 and updates direct destinations, including every entry in ON GOTO and ON GOSUB lists. Omitted-THEN command and assignment branches are recognised when the statement boundary can be proved. A compact ON ERROR handler is extracted behind an explicit ON ERROR GOTO target and a normal-flow jump over the handler. A line that is one AmigaDOS command remains a physical unit, because the rest of it is command text. Nothing changes until ✓ is selected and confirmed; × discards the proposal untouched. The accepted rewrite is one undoable operation and retains the logical cursor and viewport.</li>
              <li><strong>Tools → Condense selection or program</strong> is the safe inverse. It uses colons to pack adjacent statements into the fewest tokenised lines allowed by the real AmigaBASIC line limit. Explicit target lines begin a new packed line. Inline IF scope, ON ERROR handlers, comments, unconditional transfers and structured branch boundaries are never crossed. Programs with computed line destinations or ERL-dependent behaviour are refused rather than guessed. Surviving line numbers are retained. Condense also uses the ✓/× proposal, one-step undo and viewport preservation.</li>
              <li>BASIC subprograms, FOR and WHILE loops and structured IF and SELECT CASE blocks have minus controls in the left gutter. Select one to collapse that block and use its plus control to restore it. The single View command reads <strong>Collapse all blocks</strong> when everything is expanded and <strong>Expand all blocks</strong> when anything is collapsed. Folding never changes the real textarea or saved program. Double-click an outline line to expand everything and continue editing there. Every file initially opens fully expanded.</li>
              <li><strong>View → Structure guidance</strong> draws live 2, 4, or 8-character guide steps beside the editable BASIC source and highlights the innermost procedure, function, loop or structured conditional containing the caret. It is presentation only and never inserts indentation, replaces the textarea, changes dirty state or alters saved image bytes.</li>
              <li>Subprograms and multi-line functions receive consistent guide levels from <code>SUB name</code> or <code>DEF FNname</code> to <code>END SUB</code> or the function's leading <code>=</code> return. Closers later on a line, such as <code>NEXT:END SUB</code>, are recognised. A one-line <code>DEF FNname(...)=expression</code> does not open a block. Folding uses the same scanner.</li>
              <li>Structure guidance classifies Refactor's generated lines immediately using the same scanner as folding. A classic <code>IF condition THEN line</code> controls one statement and does not open a multi-line block, so later physical lines reached through branching or fall-through are not shown inside it. The saved program remains free of display-only indentation.</li>
              <li>Other readable files open in the text editor. Binary files open as editor-style 68000-family source. Proven register values, library call purposes, branch conditions, custom-chip and CIA register accesses, entry points and cross-references appear as semicolon comments on the relevant instruction. Internal targets receive stable labels derived from proved behaviour, such as <code>write_text_8120</code>, <code>open_close_file_834A</code>, <code>loop_8057</code> or <code>equal_80C2</code>, instead of anonymous subroutine/location names. The hexadecimal suffix keeps similar routines distinct. The analyser drops register assumptions at uncertain control-flow joins instead of inventing values. The readable-string list filters out accidental punctuation and number runs; select a string to jump to its disassembled line. Double-click an instruction only when you want that offset in Hex.</li>
              <li>Every disassembly row has hover help, including condition and size variants, unfamiliar decoder mnemonics and pseudo-operations such as <code>DC.B</code> and <code>DC.W</code>. Help combines the operation family, exact operand and addressing form, encoded bytes, cross-references and the analyser's contextual comment. Library vectors retain their specific register conventions. The Help menu lists operations actually present as well as its instruction and library reference.</li>
              <li>The disassembly <strong>Project</strong> menu retains notes, bookmarks, symbols, offset-bound comments and code/data decisions outside the image bytes. Click one row or shift-click a range, then mark it as code, text, bytes, words, addresses or bitmap data. The listing is rebuilt using that decision. Every word and long region uses big-endian values, because every 68000-family processor reads them that way. Symbols apply to every supported processor and use a portable <code>&amp;address = label</code> text format for import and export. Find references and the outline show direct callers and labelled entry points.</li>
              <li><strong>Tools → Inspect selected data</strong> presents bounded text, bytes, little-endian and big-endian words, plus a one-bit bitmap preview. Project metadata has one manager for notes, symbols, comments, bookmarks and portable JSON. A saved line comment remains attached to its exact file offset and is rendered beside the instruction. <strong>Compare with saved file</strong> displays saved and current source side by side.</li>
              <li><strong>Edit and reassemble</strong> is enabled only when <code>AMIGA_FILE_ASSEMBLER_COMMAND</code> contains <code>{source}</code> and <code>{output}</code>. It opens generated label-oriented assembly for review and requires confirmation before checksum-guarded replacement of the complete binary. <strong>Debug from selected address</strong> uses a configured <code>AMIGA_FILE_DEBUGGER_COMMAND</code>; the return status and output are retained in project history.</li>
              <li><strong>Tools → Run… / Debug…</strong> appears in every pane whose media can be attached to the configured machine. Floppy images and DMS archives mount directly in a drive. A hard drive is attached whole, from its partition table, with a profile that declares a mass-storage interface: the app copies the working <code>.hdf</code> to a private file and attaches that, so the image you are editing is isolated from emulator writes.</li>
              <li><strong>Project → Run in configured emulator</strong> appears in source and disassembly editors. Choose a hardware profile in the Workbench and apply it to the pane. Every machine from an Amiga 500 to a 4000 or CD32 runs under the bundled FS-UAE build, which is the one managed emulator. BASIC offers Inject and run/debug BASIC buffer, Mount and boot parent, or Mount parent only. Injection tokenises the current editor text into a temporary bootable floppy as <code>PROGRAM</code>, so unsaved changes are included but companion files are not. Parent choices retain dependencies and appear only if that emulator supports the container. The running machine appears in a live browser display. Click the display before typing, use Full screen when useful, and choose Stop and close to end the emulator cleanly. Capability and error text always names the effective pane emulator. <strong>Emulator and debugger results</strong> retains return status and output. Errors and notices raised while an editor is open are displayed inside that editor window, above its content, so they cannot be hidden behind the modal backdrop.</li>
              <li>Editor tabs, unsaved drafts, selection and scroll position survive a refresh in bounded browser-session storage. <strong>Open from image…</strong> searches every partition of a hard drive and labels results with its device name and volume title.</li>
              <li>The Hex editor includes structured views for ROM, Kickstart ROM, AmigaOS modules, OFS, FFS, the Rigid Disk Block, Hardfile GEO and DMS data. A custom JSON template can describe bounded fields relative to the selected byte.</li>
              <li>Labelled disassembly regions also have left-gutter folding controls. The single state-aware <strong>View</strong> command collapses or expands all labelled regions as appropriate. Visible instruction rows retain double-click-to-Hex while other regions are folded.</li>
              <li>ZIP, TAR, compressed TAR, GZIP, BZIP2 and XZ files are marked as archives. Double-click one to browse its safe file and folder hierarchy in the pane; use breadcrumbs or <strong>..</strong> to move up. Double-click a member to extract it in memory and open the normal BASIC, command-script, text, disassembly or hex viewer. Readable members can be edited: Save verifies both hashes, rebuilds the complete container and checkpoints the outer image. A DMS member additionally needs a complete one-to-one standard-block reconstruction and an unchanged encoded length; its before-save view proves that all timing and control chunks are retained. Parent traversal, non-regular TAR objects, archives over 512 MiB, members over 128 MiB and catalogues reaching 20,000 entries are rejected rather than processed without a safe bound.</li>
              <li>Use <strong>Tools → Open raw bytes in Hex</strong> from any file viewer when the automatic interpretation is uncertain. File saves retain Amiga load, execution and filetype metadata, reject stale edits and create an undo checkpoint.</li>
              <li>The shared structural scanner understands AmigaBASIC's block forms and its typed variables, and carries explicit AmigaBASIC 1.0 and 1.2 capability profiles. Diagnostics flag statements that need the later release. AmigaBASIC 1.0 programs with a recognised trailing payload are editable because only the tokenised prefix is replaced and the payload is preserved byte for byte. AmigaBASIC 1.2 remains read-only because rewriting its extended tokens as AmigaBASIC 1.0 would be unsafe.</li>
              <li>Choose <strong>Check loader dependencies</strong> to resolve CHAIN, Execute, Run, LOAD and CD targets beside the launcher, and to flag volume-rooted references before software is moved below the volume root.</li>
            </ol></div>
            <figure><img src="/help/file-editor-script.png" alt="Command-script editor showing a real OFS Startup-Sequence file"><figcaption>Command scripts remain unnumbered and preserve their execution order. Save keeps the file's Amiga metadata.</figcaption></figure>
            <figure><img src="/help/file-editor-basic.png" alt="AmigaBASIC editor showing a tokenised loader with syntax colour and folding controls"><figcaption>Tokenised AmigaBASIC 1.0 opens as editable numbered source. Folding and visual indentation do not alter the saved program.</figcaption></figure>
            <figure><img src="/help/file-editor-disassembly.png" alt="Annotated 68000 disassembly with address, bytes, instruction and annotation columns"><figcaption>Binary files open as bounded MC68000, 68010, 68020, ARM or 68000 disassembly. Comments remain beside the instruction they describe and the original bytes remain available through Hex.</figcaption></figure>
            <div class="help-task"><h4>Audit a collection</h4><ol>
              <li>Choose <strong>Collection</strong> in the application header, or <strong>Library → Private collection</strong> in a pane, to open the persistent catalogue. Add an open image with an optional SD-card label, NAS path or physical location and its target machines. The web edition stores complete manifests and hashes in origin-scoped IndexedDB. The Linux desktop edition uses an atomic, mode-0600 XDG client-state file. Neither catalogue contains image bytes.</li>
              <li>An indexed image is marked <strong>Refresh needed</strong> when its working revision changes. Choose <strong>Refresh indexed open images</strong> to replace matching manifests only after each scan succeeds. Exact-content, normalised-title and wanted-title reports include indexed images that are no longer open.</li>
              <li><strong>Export report</strong> downloads the current findings. <strong>Back up database</strong> retains complete versioned records and the wanted list; Import can merge or replace after bounded validation. Remove selected and Clear catalogue affect only this host's index, not images or recoverable sessions. Backups accept at most 2,000 images, 1,000,000 records and a 128 MiB selected file; the complete desktop client-state document is limited to 64 MiB.</li>
              <li>Choose <strong>Search</strong> in the application header to search every distinct open image with one query. It matches filenames, protection bits, comments, datestamps, Workbench icon types, bounded BASIC or script text, useful printable strings in binary files and raw ROM banks, recognised volume titles, publishers, launch actions and stack sizes, and ROM Workbench symbols, regions, notes and comments. An 8 to 64 digit SHA-256 prefix finds exact file content and displays the complete digest. Results identify their pane, image, path or ROM bank. Selecting one restores and raises its pane, navigates to the containing location and opens the file or relevant ROM Workbench address.</li>
              <li><strong>Analyse → Find duplicates / variants</strong> groups byte-identical content by SHA-256 and likely variants by normalised volume or path name. It compares drawer names, catalogued file content and whole volumes, so the same game installed under two different names is still found.</li>
              <li>Duplicate records are listed by title, volume and path. Select records directly using the checkbox on each result row. Equivalent groups compare filenames, protection bits, comments, sizes and SHA-256 file hashes. Whole-volume matches remain available as the strongest disk-level check.</li>
              <li>A compilation receives an extra warning listing the other titles it holds, so deleting it is a deliberate choice rather than an accident.</li>
              <li><strong>Export collection manifest</strong> downloads CSV or JSON containing partitions, files, Amiga metadata and checksums.</li>
              <li><strong>Compare with open image</strong> matches two manifests by filesystem path, partition or ROM bank. It separates additions, removals, proven renames or moves, changed bytes and metadata-only edits, then joins changed raw-byte ranges for the primary image and companion descriptor. A rename is reported only when content, size and filesystem context identify one unique pair; ambiguous duplicate files remain separate additions and removals. Export JSON retains the deterministic logical fingerprints and full itemised evidence. Different filesystem families can be compared as inventories but are marked unsuitable for direct patching.</li>
              <li>For matching filesystem families, partition layouts and ROM bank sizes, <strong>Download patch</strong> creates an <code>.affpatch.zip</code> containing the reviewed operation plan and only changed payloads. Tick changes to export a selective patch or leave every box clear for the complete comparison. Selective patches derive their own final fingerprint and automatically include the parent-drawer dependencies they need. Comparison, archive creation and verification show the current catalogue, checksum or payload phase with byte or item counts, elapsed time, throughput and ETA where meaningful. Abort stops these read-only stages safely without changing either image. <strong>Analyse → Apply guarded patch</strong> first performs a read-only preflight against the exact base fingerprint and verifies every payload. It shows the base and candidate names, change totals and an itemised operation preview; Apply remains disabled until verification succeeds. Applying creates an automatic checkpoint, repeats validation before writing and checks the complete candidate fingerprint afterwards. Abort during application restores that checkpoint. Stale, corrupt and wrong-format patches are rejected and failed applications roll back.</li>
              <li><strong>Analyse → Dry-run selected items</strong> creates a versioned compatibility report without writing. Each row records its proposed target name, Amiga metadata and any filename, directory or AmigaOS filetype conversion or loss. Export the result as JSON or Markdown. A report without blocking findings can be kept with the working image; the next saved ZIP includes its canonical JSON and Markdown below <code>Compatibility/</code>. Cross-format drag, clipboard, File-menu and Online Library batches now show this shared report before their first destination write.</li>
            </ol></div>
            <figure><img src="/help/private-collection.png" alt="Private collection catalogue showing an indexed OFS image, location and target machines"><figcaption>The host-private catalogue remains searchable after an image is closed. Locations are descriptive and the database stores manifests rather than image bytes.</figcaption></figure>
            <figure><img src="/help/duplicate-check.png" alt="Duplicate review showing selectable records and equivalent disk content"><figcaption>The duplicate command lives only in Analyse. Tick the exact records to review; nothing is deleted unless the separate final review says so.</figcaption></figure>
            <div class="help-task"><h4>Profiles, recipes and projects</h4><ol>
              <li>Choose <strong>Workbench → Hardware profiles</strong>. Start from a stock Amiga 500, 500+, 600, 1200, 2000, 3000, 4000 or CD32 profile, a common disk or mass-storage configuration, or the supplied hardfile custom system.</li>
              <li>Select the base machine in the left column, then build its hardware in the wider right column. Kickstart, floppy interface, memory and accelerator choices use dropdowns because only one can be fitted. A PiStorm replaces the CPU, so it cannot be combined with another accelerator. Cumulative firmware, mass-storage and expansion-card groups use bounded checkboxes. The list changes with the machine, required carrier or bus expansions are added automatically, and removing a dependency clears combinations that can no longer exist.</li>
              <li>A profile also records the Library filter, filing system, FastFileSystem build, expected stack size, validation target, managed emulator, debugger, RAM and startup action. Emulator-driven additions select the closest FS-UAE configuration, processor and controller. Hardware marked <strong>Validation only</strong> still affects analysis without pretending that the emulator implements it.</li>
              <li>Save retains the profile in this host's private state. Apply attaches it to an image session. The active profile becomes the default for panes without their own profile and drives Online Library machine filtering.</li>
              <li>Choose <strong>Import recipes</strong> to save naming, group prefix, online metadata and compatibility choices. Saved recipes appear in the import planner.</li>
              <li>Choose <strong>Portable project</strong> to export the current pane windows, their geometry and stack, session references, paths, profiles and recipes. Import it on the same retained installation to restore that working context. Theme remains a local host preference.</li>
              <li>In the same screen, choose an open image and select <strong>Export workflow bundle</strong> for a deterministic rebuild package. It records the earliest retained pre-change image and GEO hashes, a guarded patch for every subsequent filesystem change, the active hardware and accepted compatibility decisions, and the exact expected output hashes. The original image bytes are not included. Extract the ZIP and follow its README; <code>recipe-run</code> refuses a changed base, descriptor, patch or rebuilt result. Edited legacy sessions without a retained base, DMS archives and HFE track containers are rejected rather than represented as safely reproducible.</li>
            </ol></div>
            <div class="help-task"><h4>Monitor, abort and resume jobs</h4><ol>
              <li>Choose <strong>Jobs</strong> in the header. Running, paused, failed, completed and interrupted work remains visible after its foreground dialog closes.</li>
              <li>Abort requests stop at the next safe filesystem boundary.</li>
              <li>Resumable bulk jobs retain their request, completed items and skipped items. Choose <strong>Resume</strong> to submit only the remaining items.</li>
              <li>After a container restart, an unfinished job is marked interrupted instead of disappearing. Use Resume after checking the destination pane.</li>
            </ol></div>
            <figure><img src="/help/workbench-analysis.png" alt="Amiga File Forge Workbench and image analysis tools"><figcaption>Workbench holds reusable settings; each pane's Analyse menu runs checks against the currently open image.</figcaption></figure>
          </section>
          <section id="help-deployment">
            <h3>Build media for real hardware</h3>
            <p class="help-lead">The deployment assistant creates a checked card, USB or host-directory tree without changing the image open in the workspace.</p>
            <div class="help-task"><h4>Create a deployment package</h4><ol>
              <li>Apply the intended machine and expansions in <strong>Workbench → Hardware profiles</strong>.</li>
              <li>Open the image and choose <strong>Tools → Build hardware deployment</strong>.</li>
              <li>Choose Gotek/FlashFloppy, FastFileSystem, Hardfile, PiStorm or AmigaOS. Unavailable targets stay disabled and explain the required source format.</li>
              <li>For Gotek, choose Native filenames or an Indexed <code>DSKA0000</code> layout and its first index.</li>
              <li>Select <strong>Validate layout</strong>. A disposable sparse snapshot is hardware-finalised and hashed, so HDA/GEO checks cannot alter the live pane.</li>
              <li>Review every path, role, size, SHA-256 value, profile warning and installation step. Blocking findings disable download.</li>
              <li>Select <strong>Download deployment ZIP</strong>. If the image changed after review, validate again instead of building from a stale decision.</li>
              <li>Extract to a temporary directory, back up the known-good card or USB device, then merge the generated tree. Complete the read, write and reboot tests in its README before retiring the backup.</li>
            </ol></div>
            <div class="help-table-wrap"><table class="help-table"><thead><tr><th>Target</th><th>Generated layout</th><th>Important manual step</th></tr></thead><tbody>
              <tr><td>Gotek</td><td><code>GOTEK-USB</code>, optional <code>FF.CFG</code></td><td>Copy its contents to a firmware-compatible USB device</td></tr>
              <tr><td>FastFileSystem</td><td><code>SD-CARD/AMIGA.HDF</code></td><td>Preserve the matching ROM build and STACK configuration</td></tr>
              <tr><td>Hardfile</td><td><code>SD-CARD/Hardfile0/scsi0.hda</code> and <code>scsi0.geo</code></td><td>Keep the validated pair together</td></tr>
              <tr><td>PiStorm</td><td>Root HDF or <code>Hardfile0</code> merge tree</td><td>Preserve firmware, <code>PiStorm.cfg</code> and saved state</td></tr>
              <tr><td>AmigaOS</td><td><code>AMIGA-HOST/Images</code></td><td>Attach with geometry appropriate to the actual controller or emulator</td></tr>
            </tbody></table></div>
            <figure><img src="/help/hardware-deployment-assistant.png" alt="Hardware deployment assistant listing a Gotek target file, checksum and installation checks"><figcaption>The plan is built from the exact source revision. Its manifest and compatibility report travel with the generated media tree.</figcaption></figure>
          </section>
          <section id="help-saving">
            <h3>Save, close and recover safely</h3>
            <div class="help-task">
              <h4>Keep your changes</h4>
              <ol>
                <li>Look for the orange changed dot in the pane heading.</li>
                <li>Select the <strong>Save Image</strong> icon in the pane heading. The progress bar consistently covers validation, checksums, catalogue generation and complete ZIP construction for every format.</li>
                <li>The ready dialog appears only when the timestamped ZIP is actually complete. The automatic browser download should start immediately; use the dialog's direct <strong>Download ZIP</strong> link if it does not appear.</li>
                <li>Any validation, checksum or archive failure remains inside the app instead of replacing the page with a JSON response.</li>
                <li>Once preparation succeeds, the orange changed dot clears in every pane showing that image. It returns after the next edit. A failed save leaves the dot visible.</li>
                <li>Every save is a ZIP named with the image name and current date/time. This avoids duplicate <code>-edited</code> downloads.</li>
                <li>Every ZIP contains <code>README.md</code> with checksums, target hardware, compatibility warnings, practical restore notes and a complete catalogue. Hard-drive documentation includes the complete partition table, each partition's filing system, size and boot files.</li>
                <li>HDA/GEO pairs stay together in a <code>Hardfile0</code> directory inside the ZIP. Edited HFE images are encoded and sector-verified before downloading.</li>
                <li>Keep the original image until the edited download has been checked in an emulator or on a copy of the target media.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Recover after a refresh or interrupted download</h4>
              <ol>
                <li>Use any empty pane. If none is displayed, select <strong>Add Pane</strong>. There is no fixed pane-count limit, so close a pane only when it is no longer useful or use its <strong>Load New Image</strong> heading button to open a replacement.</li>
                <li>Select <strong>Recover previous session</strong>.</li>
                <li>Choose the retained working image. The newest session is selected first and each entry shows its name, size and last-change time.</li>
                <li>Select <strong>Recover session</strong>. Completed edits, the HDA/GEO pairing and the target-hardware profile are restored.</li>
                <li>Check the current directory, then select Save again.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Keep recovery private or clear old sessions</h4>
              <ol>
                <li>Web recovery is tied to an opaque identity kept in both a private cookie and this site's browser storage. Either copy restores the other after a restart. The Linux desktop edition instead keeps a stable mode-0600 owner ID in its XDG configuration directory. Another web profile or Linux user receives a different identity and cannot list, open or delete your sessions.</li>
                <li>In the recovery dialog, select <strong>Clear selected</strong> to delete one old working copy, or <strong>Clear all previous</strong> to delete every previous copy shown. Images currently open in any pane are protected from this list.</li>
                <li>Clearing removes only retained server working data. It never deletes the original file previously selected from your computer.</li>
                <li>Clearing both this site's cookies and browser storage removes the web identity. Deleting the desktop <code>owner-id</code> file has the same effect for the Linux application. Keep the identity while recoverable work remains important, and download finished images before clearing site data or resetting desktop configuration.</li>
              </ol>
            </div>
            <div class="help-task">
              <h4>Close or discard a working image</h4>
              <ol>
                <li>Select × in the pane heading, or on an empty pane, to remove that whole pane from the workspace. A changed image offers Save and close, Close without saving, or Cancel. Closing only detaches the image and keeps its server-side working copy.</li>
                <li>Use <strong>Recover previous session</strong> to reopen the image with its completed changes.</li>
                <li>To remove retained storage permanently, use <strong>Clear selected</strong> in the recovery dialog and confirm the deletion.</li>
              </ol>
            </div>
            <div class="help-note"><strong>Two layers of safety:</strong> editing never writes to the source selected in your browser, and automatic undo points protect recent working-copy changes. Named checkpoints are ideal before large deletions, compaction or bulk menu work.</div>
          </section>
          <section id="help-shortcuts">
            <h3>Keyboard and mouse reference</h3>
            <dl>
              <dt>Click</dt><dd>Select one item.</dd>
              <dt>Ctrl/Cmd-click</dt><dd>Add or remove an item from the selection.</dd>
              <dt>Shift-click</dt><dd>Select a continuous range.</dd>
              <dt>Ctrl/Cmd-A</dt><dd>Select every usable item in the current view.</dd>
              <dt>Ctrl/Cmd-X</dt><dd>Cut the selected items for one safe paste.</dd>
              <dt>Ctrl/Cmd-C</dt><dd>Copy the selected items for one paste.</dd>
              <dt>Ctrl/Cmd-V</dt><dd>Paste into the current drawer.</dd>
              <dt>Escape</dt><dd>Cancel a pending clipboard selection when no dialog is open.</dd>
              <dt>Double-click / Enter</dt><dd>Open a directory or HDF disk.</dd>
              <dt>Double-click a file</dt><dd>Open the content-aware BASIC, script, text, disassembly or hex editor.</dd>
              <dt>Delete</dt><dd>Delete the selected object after confirmation.</dd>
              <dt>Drag selected files</dt><dd>Copy them to a compatible destination.</dd>
              <dt>Alt+Left / Alt+Right on pane grip</dt><dd>Move a pane without dragging it.</dd>
              <dt>Breadcrumb</dt><dd>Jump directly to an ancestor directory.</dd>
              <dt>Refresh ↻</dt><dd>Reread the current view while preserving useful selection state.</dd>
            </dl>
          </section>
          <section id="help-accessibility">
            <h3>Accessibility and appearance</h3>
            <p class="help-lead">The interface targets WCAG 2.2 AA in both its Amiga 500 light theme and complementary dark theme.</p>
            <ul>
              <li>Use the first keyboard link, <strong>Skip to workspace</strong>, to bypass the header. All buttons, menus, rows, form controls and dialogs have visible keyboard focus.</li>
              <li>Press Tab and Shift-Tab to move through controls. Enter opens the focused directory or partition. Native modal dialogs and safety warnings retain keyboard focus until they close.</li>
              <li>The <strong>Light / Dark</strong> button follows the operating-system preference on first use and remembers your choice. Both palettes meet AA text contrast, and control boundaries and focus indicators meet non-text contrast requirements.</li>
              <li>Selection, access, warnings, errors and progress use words, shapes or symbols as well as colour. Status and error regions are announced to screen readers.</li>
              <li>Browser zoom and narrower windows are supported. With reduced motion enabled in the operating system, non-essential transitions and animations are suppressed.</li>
            </ul>
            <div class="help-note"><strong>Theme maintenance:</strong> the palette is isolated in <code>theme.css</code>. Layout and component geometry remain in <code>styles.css</code>, so a replacement palette can be reviewed for contrast without changing the application structure.</div>
          </section>
          <section id="help-limits">
            <h3>Compatibility, limits and troubleshooting</h3>
            <h4>Important compatibility limits</h4>
            <ul>
              <li>The bundled Amiganut engine creates and edits every writable AmigaDOS DOS type (OFS, FFS and their international and directory-cache variants) on floppies, RDB hard drives and a bare Hardfile HDA with its matching GEO.</li>
              <li>An AmigaDOS name is up to 30 characters and may not contain a colon or a slash; a full stop is an ordinary character. A directory has no fixed entry limit: its 72-entry hash table chains collisions, so a drawer holds as many files as the volume has free blocks. The pane and bulk planner use those detected limits.</li>
              <li>A hard-drive image whose volume begins at an emulator's own header offset is content-detected and retains that layout.</li>
              <li>“Physical HDD” means a byte-for-byte RAW image. The browser and container do not access devices such as <code>/dev/sdb</code> directly.</li>
              <li>A DMS track is editable only under the archive project's same-length structural proof. Convert the archive back to a disk for unrestricted filesystem editing.</li>
              <li>HFE v2/v3, bad-sector and advanced track images open read-only. Clean sector-based HFE v1 images can be edited and are verified again when saved.</li>
              <li>Metadata is preserved only where the destination filing system has an equivalent field.</li>
            </ul>
            <h4>When something does not work</h4>
            <dl>
              <dt>Button is disabled</dt><dd>Select a suitable item first, or wait for the current pane operation to finish.</dd>
              <dt>Invalid filename</dt><dd>Use the prompted replacement. An AmigaDOS file, drawer or volume name allows up to 30 Latin-1 characters and excludes the colon and both slashes; a partition device name allows up to 31 and Kickstart ROM leaves up to 30. Leading or trailing whitespace, path syntax and control characters are rejected. The compatibility review normalises and truncates before writing, then checks case-insensitive clashes within each destination directory.</dd>
              <dt>Not enough space</dt><dd>Delete unwanted data, compact the filesystem, or create a larger destination. OFS keeps 24 bytes of header in every data block, so it stores 488 bytes per block against the full 512 bytes FFS uses and fits noticeably less on the same disk.</dd>
              <dt>A high-density disk will not open on this machine</dt><dd>The 1760 KiB format is written only by the HD drives of the A3000 and A4000. Every other Amiga reads DS/DD, so a target set to an A500, A600 or A1200 refuses a high-density image rather than offering a disk that machine could never read.</dd>
              <dt>HFE is read-only</dt><dd>The image uses HFE v2/v3, reports bad sectors, or contains track features the sector editor cannot reproduce safely. Export its files or copy its readable sectors to another image.</dd>
              <dt>HxCFE is reported missing</dt><dd>Official Docker images and native 1.0.2 packages include HxCFE and its supporting libraries. Reinstall the package matching the host distribution and architecture if <code>/opt/amiga-file-forge/native/bin/hxcfe</code> is absent. A source checkout receives HxCFE when its Docker image or native package is built.</dd>
              <dt>An AmigaDOS image cannot be opened</dt><dd>Confirm it is a raw AmigaDOS image or a supported HDF layout rather than a compressed archive or a flux capture. The detailed error distinguishes an unrecognised filesystem from a corrupt map or directory.</dd>
              <dt>Name collision found</dt><dd>Use the default DISC-0000 naming strategy, or review every highlighted name. The check is case-insensitive and scoped to each destination parent.</dd>
              <dt>Empty disk found</dt><dd>Choose Skip and continue or Abort. A blank disk does not become an empty drawer.</dd>
              <dt>Destination exists</dt><dd>An empty directory is reused silently. A populated directory offers Keep, Replace or Abort; a file is never overwritten as though it were an empty directory.</dd>
              <dt>HDA geometry error</dt><dd>A bare Hardfile HDA needs its exact matching GEO file, because surfaces, blocks per track and cylinders are recorded there rather than in the image. An image carrying a Rigid Disk Block describes its own geometry and needs no sidecar.</dd>
              <dt>Network error</dt><dd>Keep the dialog open, inspect its detailed stage, refresh the destination pane if necessary, then use retry. Online metadata can be entered manually.</dd>
              <dt>View appears stale</dt><dd>Select ↻ in that pane. In a partition use All partitions, not the root breadcrumb, to return to the partition table.</dd>
              <dt>A refresh shows the start screen</dt><dd>Current owner-isolated panes and their open directories are restored automatically after a normal refresh. On the first refresh after upgrading from an older version, the newest retained working session for that owner is reopened as a bridge. Closing a pane deliberately removes it from auto-restore while retaining its recovery copy.</dd>
            </dl>
            <div class="help-note"><strong>Launcher rule:</strong> when a disk contains DiskMenu it is preferred over the Startup-Sequence and launched with Run, or with AmigaBASIC when it is a saved BASIC program. Otherwise Amiga File Forge inspects Startup-Sequence and conventional loaders to choose the safest action and stack size.</div>
            <div class="help-warning"><strong>Stack safety:</strong> every imported title records the stack size derived from its selected launcher in the actual image. An Execute record follows the script it runs to whatever <code>Stack</code> command that script issues; an AmigaBASIC record carries the interpreter's own default. A record that runs a program directly is identified as not issuing a <code>Stack</code> command at all. Changing a derived value opens a Yes/Cancel warning because an incorrect STACK can overwrite filing-system or loader workspace and cause corrupted BASIC, hangs or crashes on real hardware.</div>
            <div class="help-note"><strong>Best practice:</strong> work from copies, create named checkpoints, download finished images, validate after large operations, and test the result before restoring it to real media.</div>
          </section>
          <section id="help-project">
            <h3>Project and support</h3>
            <p class="help-lead">Amiga File Forge is an open-source project. Its documentation covers installation, every supported media family, the file editors, ROM maintenance, firmware and release validation.</p>
            <div class="help-note"><strong>Confirm the running build:</strong> close this handbook, then choose <strong>Help → About Amiga File Forge</strong>. The About dialog reports the version returned by the current server, the web or Linux desktop edition, filesystem engine, licence and project links.</div>
            <div class="help-task"><h4>Choose the detailed reference</h4><ul>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer">Documentation index</a>: a task and capability map for the complete handbook.</li>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/README.md" target="_blank" rel="noopener noreferrer">Product and media handbook</a>: formats, restrictions, workflows, architecture, configuration and tests.</li>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/docs/INSTALLATION.md" target="_blank" rel="noopener noreferrer">Installation and operations</a>: desktop and Raspberry Pi builds, ports, sessions, updates, backups and diagnostics.</li>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/docs/FILE-EDITOR-GUIDE.md" target="_blank" rel="noopener noreferrer">File editor and code analysis</a>: BASIC, scripts, disassembly, archives, binary synchronisation and emulator hand-off.</li>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/docs/ROM-GUIDE.md" target="_blank" rel="noopener noreferrer">ROM image handbook</a>: banks, commands, decoded regions, Kickstart ROM, Workbench, programmers and projects.</li>
              <li><a href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/docs/CLI-GUIDE.md" target="_blank" rel="noopener noreferrer">Headless CLI and deterministic recipes</a>: Docker invocation, stable JSON results, dry-runs, source identity checks and repeatable image builds.</li>
            </ul></div>
            <div class="help-task"><h4>Get the code or report a problem</h4><ol>
              <li>Visit <a href="https://github.com/peteclarke-del/AmigaFileForge" target="_blank" rel="noopener noreferrer">github.com/peteclarke-del/AmigaFileForge</a>.</li>
              <li>When reporting a problem, include the image format, target hardware profile, operation, visible error and whether the original image still opens correctly.</li>
              <li>Do not attach commercial disk images unless you have permission to share them. A catalogue, screenshot and exact error are often enough to start investigating.</li>
              <li>The repository and its source archives do not include the local <code>samples/</code> directory. Developers can place their own test images there without adding them to Git, <code>git archive</code> output or the Docker build context.</li>
            </ol></div>
            <div class="help-note"><strong>Saved archives are self-documenting:</strong> every downloaded ZIP contains a README with the image details, checksum, target profile, warnings and catalogue, plus a link back to the current project documentation.</div>
          </section>
        </div>
      </div>
      <div class="modal-actions"><button class="button primary" value="cancel">Close help</button></div>
    </div>`);
  const layout = modalContent.querySelector(".help-layout");
  const content = modalContent.querySelector(".help-content");
  modalContent.querySelectorAll(".help-toc a").forEach(link => {
    link.addEventListener("click", event => {
      event.preventDefault();
      const target = modalContent.querySelector(link.getAttribute("href"));
      if (!target) return;
      const scrollHost = content.scrollHeight > content.clientHeight ? content : layout;
      const top = scrollHost.scrollTop
        + target.getBoundingClientRect().top
        - scrollHost.getBoundingClientRect().top;
      scrollHost.scrollTo({ top, behavior: "smooth" });
    });
  });
}
    return showHelp;
  }
  window.AmigaHelp = Object.freeze({ create });
})();
