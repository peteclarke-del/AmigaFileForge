window.AmigaFormats = (() => {
  // Extensions are a hint for the file picker and for drag-and-drop. The
  // backend identifies every image from its bytes, so a renamed file is
  // recognised correctly and a wrongly named one is refused with a reason.
  const imageExtensions = [
    "adf", "adz", "dms", "hdf", "hdz", "hda", "rdsk",
    "hfe", "scp", "ipf",
    "rom", "kick", "a500", "a600", "a1200", "a3000", "a4000", "cd32",
    "img", "raw", "bin", "dsk"
  ];
  const imagePattern = new RegExp(`\\.(${imageExtensions.join("|")})$`, "i");
  // A floppy-sized volume, whatever container it arrived in.
  const ofsPattern = /\.(adf|adz|dms|hfe|scp|ipf)$/i;
  const archivePattern = /\.(zip|lha|lzx)$/i;
  // Anything that may hold an AmigaDOS volume, floppy or hard drive.
  const ffsPattern = /\.(adf|adz|hda|hdf|hdz|rdsk|img|raw|bin|dsk|hfe|scp|zip|lha|lzx)$/i;

  return {
    accept: imageExtensions.map(extension => `.${extension}`).concat(".geo", ".zip", ".lha", ".lzx").join(","),
    isDescriptor: name => /\.geo$/i.test(name),
    isArchive: name => archivePattern.test(name),
    isOfsImage: name => ofsPattern.test(name) || archivePattern.test(name),
    isImage: name => imagePattern.test(name),
    isImportableImage: name => imagePattern.test(name) || archivePattern.test(name),
    isPotentialFfsImage: name => ffsPattern.test(name),
    isRomImage: name => /\.(rom[0-3]?|kick|a500|a600|a1200|a3000|a4000|cd32)$/i.test(name),
    stem: name => String(name).replace(/\.[^.]+$/, "")
  };
})();
