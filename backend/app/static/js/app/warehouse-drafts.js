import { newSubmissionKey } from './submission.js';

const databaseName = 'fz-warehouse-drafts';
const storeName = 'orders';
const sessions = new Map();
let database;

function openDatabase() {
  if (!database) {
    database = new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) {
        reject(new Error('此浏览器无法保存本机待确认单。'));
        return;
      }
      const request = indexedDB.open(databaseName, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(storeName);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('本机待确认单正在被另一个页面使用。'));
    });
  }
  return database;
}

async function useDatabase(mode, operation) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(storeName, mode);
    const request = operation(transaction.objectStore(storeName));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onabort = () => reject(transaction.error || request.error);
    transaction.onerror = () => reject(transaction.error || request.error);
  });
}

function blankDraft() {
  return { key: newSubmissionKey(), items: [], photos: {}, discrepancies: {}, versions: {}, checkout: false, review: [], attempted: false };
}

export async function warehouseDraft(userId, mode) {
  const key = `${userId}:${mode}`;
  if (!sessions.has(key)) {
    sessions.set(key, (async () => {
      const session = { key, draft: blankDraft(), writes: Promise.resolve(), uploading: false, storageError: false, refresh: null };
      try {
        const saved = await useDatabase('readonly', store => store.get(key));
        if (saved) session.draft = { ...session.draft, ...saved };
      } catch {
        session.storageError = true;
      }
      return session;
    })());
  }
  return sessions.get(key);
}

export function saveWarehouseDraft(session) {
  const draft = session.draft;
  const snapshot = {
    ...draft,
    items: draft.items.map(item => ({ ...item, allocations: item.allocations.map(allocation => ({ ...allocation })) })),
    photos: Object.fromEntries(Object.entries(draft.photos).map(([key, photo]) => [key, { ...photo }])),
    discrepancies: Object.fromEntries(Object.entries(draft.discrepancies).map(([key, report]) => [key, { ...report }])),
    versions: { ...draft.versions },
    review: [...draft.review],
  };
  session.writes = session.writes.then(async () => {
    try {
      await useDatabase('readwrite', store => store.put(snapshot, session.key));
      session.storageError = false;
    } catch {
      session.storageError = true;
      session.refresh?.();
    }
  });
  return session.writes;
}

export async function clearWarehouseDraft(session) {
  session.draft = blankDraft();
  session.writes = session.writes.then(async () => {
    try {
      await useDatabase('readwrite', store => store.delete(session.key));
      session.storageError = false;
    } catch {
      session.storageError = true;
    }
  });
  await session.writes;
}

const jpeg = (canvas, quality) => new Promise((resolve, reject) => {
  canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('照片压缩失败，请重新拍摄。')), 'image/jpeg', quality);
});

export async function compressWarehousePhoto(file) {
  if (file.size > 25 * 1024 * 1024) throw new Error('照片超过 25 MB，请重新拍摄。');
  if (file.type === 'image/svg+xml' || /\.svg$/i.test(file.name)) throw new Error('请选择现场照片。');
  const source = URL.createObjectURL(file);
  try {
    const photo = new Image();
    await new Promise((resolve, reject) => {
      photo.onload = resolve;
      photo.onerror = () => reject(new Error('照片无法读取，请重新拍摄。'));
      photo.src = source;
    });
    if (!photo.naturalWidth || photo.naturalWidth * photo.naturalHeight > 60_000_000) {
      throw new Error('照片尺寸过大，请重新拍摄。');
    }
    const canvas = document.createElement('canvas');
    const longest = Math.max(photo.naturalWidth, photo.naturalHeight);
    for (const side of [1440, 1280, 1120, 1000]) {
      const scale = Math.min(1, side / longest);
      canvas.width = Math.max(1, Math.round(photo.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(photo.naturalHeight * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('当前浏览器无法处理照片。');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(photo, 0, 0, canvas.width, canvas.height);
      let readableFallback;
      for (const quality of [0.8, 0.72, 0.64]) {
        const blob = await jpeg(canvas, quality);
        if (blob.size <= 375 * 1024) return blob;
        if (!readableFallback && blob.size <= 500 * 1024) readableFallback = blob;
      }
      if (readableFallback) return readableFallback;
    }
    const blob = await jpeg(canvas, 0.5);
    if (blob.size > 500 * 1024) throw new Error('照片压缩后仍过大，请重新拍摄。');
    return blob;
  } finally {
    URL.revokeObjectURL(source);
  }
}
