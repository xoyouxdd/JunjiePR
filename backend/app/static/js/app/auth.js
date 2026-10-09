// Logout and authenticated portal redirect.
import { api } from './api.js';
import { portalPath } from './format.js';

async function logout(){await api('/api/logout',{method:'POST'});location.href=portalPath('/login')}

export { logout };
