class AuthManager {
  constructor() {
    this.serverUrl = 'http://127.0.0.1:8888';
    this.extensionId = chrome.runtime.id;
  }

  async login() {
    try {
      const loginUrl = `${this.serverUrl}/extension/login?extension_id=${this.extensionId}`;
      
      const result = await chrome.identity.launchWebAuthFlow({
        url: loginUrl,
        interactive: true
      });

      const token = this.extractTokenFromUrl(result);
      if (token) {
        await chrome.storage.local.set({ 'firebase_token': token });
        return { success: true, token };
      } else {
        throw new Error('No token received');
      }
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: error.message };
    }
  }

  extractTokenFromUrl(url) {
    const match = url.match(/#token=([^&]+)/);
    return match ? match[1] : null;
  }

  async getStoredToken() {
    const result = await chrome.storage.local.get(['firebase_token']);
    return result.firebase_token;
  }

  async logout() {
    await chrome.storage.local.remove(['firebase_token']);
  }

  async isLoggedIn() {
    const token = await this.getStoredToken();
    if (!token) return false;

    try {
      const response = await this.makeAuthenticatedRequest('/api/user/tokens', 'GET');
      return response.ok;
    } catch (error) {
      return false;
    }
  }

  async makeAuthenticatedRequest(endpoint, method = 'GET', data = null) {
    const token = await this.getStoredToken();
    if (!token) {
      throw new Error('Not authenticated');
    }

    const options = {
      method,
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    };

    if (data && (method === 'POST' || method === 'PUT')) {
      options.body = JSON.stringify(data);
    }

    return fetch(`${this.serverUrl}${endpoint}`, options);
  }
}

class ApiClient {
  constructor(authManager) {
    this.auth = authManager;
  }

  async setClientStatus(firebaseUid, name, artist, source) {
    const data = { name, artist, source };
    const response = await this.auth.makeAuthenticatedRequest(
      `/api/set_client_status/${firebaseUid}`,
      'POST',
      data
    );
    return response.json();
  }

  async setPriority(firebaseUid, priorityList) {
    const data = { list: priorityList };
    const response = await this.auth.makeAuthenticatedRequest(
      `/api/set_priority/${firebaseUid}`,
      'POST',
      data
    );
    return response.json();
  }

  async getUserStatus(firebaseUid) {
    const response = await this.auth.makeAuthenticatedRequest(
      `/api/user/status/${firebaseUid}`,
      'GET'
    );
    return response.json();
  }

  async startSlackSync(firebaseUid) {
    const response = await this.auth.makeAuthenticatedRequest(
      `/sync/slack/start/${firebaseUid}`,
      'POST'
    );
    return response.json();
  }

  async stopSlackSync(firebaseUid) {
    const response = await this.auth.makeAuthenticatedRequest(
      `/sync/slack/stop/${firebaseUid}`,
      'POST'
    );
    return response.json();
  }

  async getSlackSyncStatus(firebaseUid) {
    const response = await this.auth.makeAuthenticatedRequest(
      `/sync/slack/status/${firebaseUid}`,
      'GET'
    );
    return response.json();
  }
}

const authManager = new AuthManager();
const apiClient = new ApiClient(authManager);

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'login') {
    authManager.login().then(sendResponse);
    return true;
  }
  
  if (request.action === 'logout') {
    authManager.logout().then(() => sendResponse({ success: true }));
    return true;
  }
  
  if (request.action === 'isLoggedIn') {
    authManager.isLoggedIn().then(sendResponse);
    return true;
  }
  
  if (request.action === 'getCurrentUser') {
    getCurrentUser().then(sendResponse);
    return true;
  }
  
  if (request.action === 'setStatus') {
    apiClient.setClientStatus(
      request.firebaseUid,
      request.name,
      request.artist,
      request.source
    ).then(sendResponse);
    return true;
  }
  
  if (request.action === 'setPriority') {
    apiClient.setPriority(request.firebaseUid, request.priorityList).then(sendResponse);
    return true;
  }
  
  if (request.action === 'getUserStatus') {
    apiClient.getUserStatus(request.firebaseUid).then(sendResponse);
    return true;
  }

  if (request.action === 'startSlackSync') {
    apiClient.startSlackSync(request.firebaseUid).then(sendResponse);
    return true;
  }

  if (request.action === 'stopSlackSync') {
    apiClient.stopSlackSync(request.firebaseUid).then(sendResponse);
    return true;
  }

  if (request.action === 'getSlackSyncStatus') {
    apiClient.getSlackSyncStatus(request.firebaseUid).then(sendResponse);
    return true;
  }
});

function decodeJWT(token) {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
      return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(''));
    return JSON.parse(jsonPayload);
  } catch (error) {
    console.error('JWT decode error:', error);
    return null;
  }
}

async function getCurrentUser() {
  const token = await authManager.getStoredToken();
  if (!token) return null;
  
  const decoded = decodeJWT(token);
  return decoded ? {
    uid: decoded.user_id || decoded.sub,
    email: decoded.email
  } : null;
}