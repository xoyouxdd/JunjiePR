// Apply the browser preference before styles/first paint. No network or account data.
(() => {
  const key = 'fz.appearance.skin.v1';
  const artworkKey = 'fz.appearance.artwork.v1';
  const assetBase = new URL('../images/avatars/', document.currentScript.src);
  const validArtwork = value => value === 'medal' || /^avatar:[a-z0-9][a-z0-9-]{0,79}$/.test(value || '') ? value : 'theme';
  let artwork = 'theme';
  try { artwork = validArtwork(localStorage.getItem(artworkKey)); } catch { /* Storage may be disabled. */ }
  const applyArtwork = () => {
    document.documentElement.dataset.headerArtwork = artwork.startsWith('avatar:') ? 'portrait' : artwork;
    if (artwork.startsWith('avatar:')) document.documentElement.style.setProperty('--header-portrait', `url("${new URL(artwork.slice(7) + '-160.webp', assetBase).href}")`);
    else document.documentElement.style.removeProperty('--header-portrait');
  };
  applyArtwork();
  const skins = ['blue', 'green', 'ivory', 'medal', 'circle'];
  const valid = value => skins.includes(value) ? value : 'blue';
  let current = 'blue';
  try { current = valid(localStorage.getItem(key)); } catch { /* Storage may be disabled. */ }
  document.documentElement.dataset.skin = current;
  window.FZAppearance = {
    get: () => current,
    getArtwork: () => artwork,
    setArtwork(value) {
      artwork = validArtwork(value);applyArtwork();
      let persisted = true;
      try { localStorage.setItem(artworkKey, artwork); } catch { persisted = false; }
      window.dispatchEvent(new CustomEvent('app:artwork-changed', {detail: {artwork, persisted}}));
      return persisted;
    },
    set(value) {
      current = valid(value);
      document.documentElement.dataset.skin = current;
      let persisted = true;
      try { localStorage.setItem(key, current); } catch { persisted = false; }
      window.dispatchEvent(new CustomEvent('app:skin-changed', {detail: {skin: current, persisted}}));
      return persisted;
    }
  };
  window.addEventListener('storage', event => {
    if (event.key === artworkKey || event.key === null) {
      artwork = validArtwork(event.key === null ? null : event.newValue);applyArtwork();
      window.dispatchEvent(new CustomEvent('app:artwork-changed', {detail: {artwork, persisted: true}}));
    }
    if (event.key !== key && event.key !== null) return;
    current = valid(event.newValue);
    document.documentElement.dataset.skin = current;
    window.dispatchEvent(new CustomEvent('app:skin-changed', {detail: {skin: current, persisted: true}}));
  });
})();
