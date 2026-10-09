// Shared session and page state. DOM roots are resolved after the module entry is deferred by the browser.


const app = document.getElementById('app');

const tabs = document.getElementById('tabs');

const state = { me: null, options: null, tab: null, pendingMaterialFocusId: null, actionBadgeTotal: 0, homeMonth: null };

const has = p => state.me.permissions.includes(p);

export { app, has, state, tabs };
