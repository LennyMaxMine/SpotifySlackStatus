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

let lastTrack = null;
let currentUserUid = null; 

chrome.storage.local.get(['firebaseUid'], (result) => {
  currentUserUid = result.firebaseUid || null;
});

async function findPlayingTrack() {
  const tabs = await chrome.tabs.query({
    url: ['*://*.youtube.com/*', '*://*.soundcloud.com/*', '*://*.music.apple.com/*']
  });

  for (const tab of tabs) {
    try {
      if (tab.url.includes('youtube.com')) {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            const video = document.querySelector('video');
            if (!video || video.paused) return null;

            const rawTitle = document.querySelector('.title.style-scope.ytd-video-primary-info-renderer')?.textContent || document.title || '';
            const artist = document.querySelector('#text-container yt-formatted-string.ytd-channel-name')?.textContent || null;

            let title = rawTitle.replace(/ *- *YouTube$/, '');

            return { title, artist, source: 'youtube' };
          }
        });
        if (result.result) return result.result;
      }

      if (tab.url.includes('soundcloud.com')) {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            const playing = document.querySelector('.playControl.playing');
            if (!playing) return null;

            const title = document.querySelector('.playbackSoundBadge__titleLink')?.textContent || null;
            const artist = document.querySelector('.playbackSoundBadge__lightLink')?.textContent 
                          || document.querySelector('.playbackSoundBadge__userLink')?.textContent 
                          || null;

            if (!title || !artist) return null;

            return { title, artist, source: 'soundcloud' };
          }
        });
        if (result.result) return result.result;
      }

      if (tab.url.includes('music.apple.com')) {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            const audio = document.querySelector('audio');
            if (!audio || audio.paused) return null;

            const title = document.querySelector('.web-chrome-playback-lcd__song-name')?.textContent || null;
            const artist = document.querySelector('.web-chrome-playback-lcd__primary-link')?.textContent 
                          || document.querySelector('.web-chrome-playback-lcd__sub-copy')?.textContent 
                          || null;

            if (!title || !artist) return null;

            return { title, artist, source: 'apple_music' };
          }
        });
        if (result.result) return result.result;
      }

    } catch (e) {
      console.error('Error checking tab', tab.url, e);
    }
  }
  return null;
}

async function checkAndUpdateTrack() {
  if (!currentUserUid) {
    chrome.storage.local.get(['firebaseUid'], (result) => {
      currentUserUid = result.firebaseUid || null;
    });
    return;
  }

  try {
    const track = await findPlayingTrack();

    if (track && (!lastTrack || track.title !== lastTrack.title || track.artist !== lastTrack.artist || track.source !== lastTrack.source)) {
      lastTrack = track;

      fetch(`http://127.0.0.1:8888/api/set_client_status/${currentUserUid}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          name: track.title,
          artist: track.artist,
          source: track.source
        })
      }).then(async res => {
        const data = await res.json();
        if (!data.success) {
          console.error('API error:', data.error);
        }
      }).catch(e => console.error('Fetch error:', e));
    }
  } catch (e) {
    console.error('Error in checkAndUpdateTrack:', e);
  }
}

setInterval(checkAndUpdateTrack, 20000);
