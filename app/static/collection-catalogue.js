(function (root) {
  "use strict";

  const BACKUP_FORMAT = "amiga-file-forge-private-collection";
  const BACKUP_VERSION = 1;
  const DATABASE = "amiga-file-forge-private-collection-v1";
  const MAX_IMAGES = 2000;
  const MAX_RECORDS = 1_000_000;

  const text = value => String(value ?? "").trim();
  const titleKey = value => text(value).toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
  const imageKey = (kind, name, location = "") => [text(kind).toLowerCase(), text(name).toLowerCase(), text(location).toLowerCase()].join("|");

  function titlesFromManifest(manifest) {
    const titles = [];
    const add = (title, publisher = "", source = "catalogue") => {
      if (!text(title)) return;
      titles.push({ title: text(title), publisher: text(publisher), source, key: titleKey(title) });
    };
    // What names software on an Amiga volume is the volume's own title and
    // the drawers installed at its root. A file deeper in the tree is part of
    // a title rather than a title of its own, so it is not indexed as one.
    add(manifest.image?.title || manifest.image?.name, "", "image");
    (manifest.records || []).forEach(record => {
      if (record.recordType === "partition") add(record.title || record.device, "", "partition");
      if (record.recordType === "rom-bank" && !record.empty) add(record.title, "", "rom-bank");
      if (record.recordType === "directory" && !String(record.path || "").includes("/")) {
        add(record.path, "", "drawer");
      }
    });
    return [...new Map(titles.map(item => [`${item.key}|${item.publisher.toLowerCase()}|${item.source}`, item])).values()];
  }

  function catalogueEntry(manifest, options = {}, previous = null, now = () => new Date().toISOString(), uuid = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`) {
    if (!manifest || !manifest.image || !Array.isArray(manifest.records) || !Array.isArray(manifest.menus)) {
      throw new Error("The collection manifest is incomplete.");
    }
    const location = text(options.location ?? previous?.location);
    const machines = [...new Set((options.machines || previous?.machines || []).map(text).filter(Boolean))];
    const timestamp = now();
    return {
      id: previous?.id || uuid(),
      imageKey: imageKey(manifest.image.kind, manifest.image.name, location),
      sessionId: text(options.sessionId || manifest.image.id),
      name: text(manifest.image.name) || "Untitled image",
      kind: text(manifest.image.kind) || "unknown",
      size: Number(manifest.image.size || 0),
      location,
      machines,
      notes: text(options.notes ?? previous?.notes),
      fingerprint: text(manifest.fingerprint),
      revision: text(manifest.revision || manifest.image.revision),
      createdAt: previous?.createdAt || timestamp,
      indexedAt: timestamp,
      stale: false,
      records: manifest.records,
      menus: manifest.menus,
      titles: titlesFromManifest(manifest),
    };
  }

  function collectionReport(entries, wanted = []) {
    const hashes = new Map();
    const variants = new Map();
    const ownedTitles = new Set();
    entries.forEach(image => {
      (image.records || []).forEach(record => {
        const hash = text(record.sha256).toLowerCase();
        if (!hash || !["file", "rom-bank"].includes(record.recordType)) return;
        const item = { imageId: image.id, image: image.name, kind: image.kind, path: record.path || "", partition: record.partition, device: record.device, bank: record.bank, title: record.title || record.path || "Untitled", sha256: hash };
        if (!hashes.has(hash)) hashes.set(hash, []);
        hashes.get(hash).push(item);
      });
      (image.titles || []).forEach(title => {
        ownedTitles.add(title.key || titleKey(title.title));
        const key = title.key || titleKey(title.title);
        if (!key) return;
        if (!variants.has(key)) variants.set(key, []);
        variants.get(key).push({ imageId: image.id, image: image.name, title: title.title, publisher: title.publisher, source: title.source });
      });
    });
    const acrossImages = rows => new Set(rows.map(row => row.imageId)).size > 1;
    return {
      images: entries.length,
      records: entries.reduce((total, entry) => total + (entry.records || []).length, 0),
      titles: ownedTitles.size,
      stale: entries.filter(entry => entry.stale).length,
      exactDuplicates: [...hashes.values()].filter(rows => rows.length > 1 && acrossImages(rows)),
      titleVariants: [...variants.values()].filter(rows => rows.length > 1 && acrossImages(rows)),
      missingTitles: wanted.map(text).filter(Boolean).filter(title => !ownedTitles.has(titleKey(title))),
    };
  }

  function validateBackup(document) {
    if (!document || document.format !== BACKUP_FORMAT || document.version !== BACKUP_VERSION || !Array.isArray(document.images)) {
      throw new Error(`Only ${BACKUP_FORMAT} version ${BACKUP_VERSION} backups are supported.`);
    }
    if (document.images.length > MAX_IMAGES) throw new Error(`A collection backup cannot contain more than ${MAX_IMAGES} images.`);
    let records = 0;
    document.images.forEach((image, index) => {
      if (!image || !text(image.id) || !Array.isArray(image.records) || !Array.isArray(image.menus)) {
        throw new Error(`Collection image ${index + 1} is incomplete.`);
      }
      records += image.records.length;
    });
    if (records > MAX_RECORDS) throw new Error(`A collection backup cannot contain more than ${MAX_RECORDS.toLocaleString()} records.`);
    return document;
  }

  function create({ indexedDB = root.indexedDB, now, uuid } = {}) {
    if (!indexedDB) return { available: false };
    let databasePromise;
    const open = () => databasePromise ||= new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        const images = database.createObjectStore("images", { keyPath: "id" });
        images.createIndex("imageKey", "imageKey", { unique: false });
        images.createIndex("sessionId", "sessionId", { unique: false });
        database.createObjectStore("settings", { keyPath: "key" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("The private collection database could not be opened."));
    });
    const request = operation => new Promise((resolve, reject) => {
      operation.onsuccess = () => resolve(operation.result);
      operation.onerror = () => reject(operation.error || new Error("The private collection operation failed."));
    });
    const store = async (name, mode = "readonly") => (await open()).transaction(name, mode).objectStore(name);
    const list = async () => request((await store("images")).getAll());
    const settings = async () => (await request((await store("settings")).get("preferences"))) || { key: "preferences", wanted: [] };
    const saveSettings = async value => request((await store("settings", "readwrite")).put({ key: "preferences", wanted: (value.wanted || []).map(text).filter(Boolean) }));
    const upsertManifest = async (manifest, options = {}) => {
      const entries = await list();
      const key = imageKey(manifest.image?.kind, manifest.image?.name, options.location);
      const previous = entries.find(entry => options.id ? entry.id === options.id : entry.sessionId === options.sessionId || entry.imageKey === key);
      const entry = catalogueEntry(manifest, options, previous, now, uuid);
      await request((await store("images", "readwrite")).put(entry));
      return entry;
    };
    const markStale = async image => {
      const entries = await list();
      const matches = entries.filter(entry => entry.sessionId === image.id || (entry.name === image.name && entry.kind === image.kind));
      const changed = matches.filter(entry => !image.revision || entry.revision !== image.revision);
      if (!changed.length) return 0;
      const imageStore = await store("images", "readwrite");
      await Promise.all(changed.map(entry => request(imageStore.put({ ...entry, stale: true }))));
      return changed.length;
    };
    const remove = async ids => {
      const imageStore = await store("images", "readwrite");
      await Promise.all(ids.map(id => request(imageStore.delete(id))));
    };
    const clear = async () => {
      await request((await store("images", "readwrite")).clear());
      await request((await store("settings", "readwrite")).clear());
    };
    const exportBackup = async () => ({
      format: BACKUP_FORMAT, version: BACKUP_VERSION, exportedAt: (now || (() => new Date().toISOString()))(),
      images: await list(), settings: await settings(),
    });
    const importBackup = async (document, replace = false) => {
      validateBackup(document);
      if (replace) await clear();
      const imageStore = await store("images", "readwrite");
      await Promise.all(document.images.map(image => request(imageStore.put(image))));
      if (document.settings) await saveSettings(document.settings);
      return document.images.length;
    };
    return { available: true, list, settings, saveSettings, upsertManifest, markStale, remove, clear, exportBackup, importBackup };
  }

  function createRemote({ load, save, now, uuid }) {
    if (typeof load !== "function" || typeof save !== "function") return { available: false };
    let serial = Promise.resolve();
    const normalise = document => ({
      images: Array.isArray(document?.images) ? document.images : [],
      settings: document?.settings && typeof document.settings === "object"
        ? document.settings
        : { key: "preferences", wanted: [] },
    });
    const read = async () => normalise(await load());
    const mutate = operation => {
      const result = serial.then(async () => {
        const document = await read();
        const value = await operation(document);
        await save(document);
        return value;
      });
      serial = result.catch(() => {});
      return result;
    };
    const settledRead = async () => {
      await serial;
      return read();
    };
    const list = async () => (await settledRead()).images;
    const settings = async () => (await settledRead()).settings;
    const saveSettings = value => mutate(document => {
      document.settings = { key: "preferences", wanted: (value.wanted || []).map(text).filter(Boolean) };
    });
    const upsertManifest = (manifest, options = {}) => mutate(document => {
      const key = imageKey(manifest.image?.kind, manifest.image?.name, options.location);
      const previous = document.images.find(entry => options.id ? entry.id === options.id : entry.sessionId === options.sessionId || entry.imageKey === key);
      const entry = catalogueEntry(manifest, options, previous, now, uuid);
      const offset = document.images.findIndex(item => item.id === entry.id);
      if (offset >= 0) document.images[offset] = entry;
      else document.images.push(entry);
      return entry;
    });
    const markStale = image => mutate(document => {
      let changed = 0;
      document.images = document.images.map(entry => {
        const matches = entry.sessionId === image.id || (entry.name === image.name && entry.kind === image.kind);
        if (!matches || !image.revision || entry.revision === image.revision || entry.stale) return entry;
        changed += 1;
        return { ...entry, stale: true };
      });
      return changed;
    });
    const remove = ids => mutate(document => {
      const selected = new Set(ids);
      document.images = document.images.filter(entry => !selected.has(entry.id));
    });
    const clear = () => mutate(document => {
      document.images = [];
      document.settings = { key: "preferences", wanted: [] };
    });
    const exportBackup = async () => {
      const document = await settledRead();
      return { format: BACKUP_FORMAT, version: BACKUP_VERSION, exportedAt: (now || (() => new Date().toISOString()))(), ...document };
    };
    const importBackup = (documentValue, replace = false) => mutate(document => {
      validateBackup(documentValue);
      if (replace) document.images = [];
      const byId = new Map(document.images.map(entry => [entry.id, entry]));
      documentValue.images.forEach(entry => byId.set(entry.id, entry));
      document.images = [...byId.values()];
      if (documentValue.settings) document.settings = documentValue.settings;
      return documentValue.images.length;
    });
    return { available: true, list, settings, saveSettings, upsertManifest, markStale, remove, clear, exportBackup, importBackup };
  }

  root.AmigaCollectionCatalogue = { BACKUP_FORMAT, BACKUP_VERSION, catalogueEntry, collectionReport, create, createRemote, imageKey, titleKey, titlesFromManifest, validateBackup };
})(typeof window === "undefined" ? globalThis : window);
