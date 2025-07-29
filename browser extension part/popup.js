let currentUser = null;

async function checkLoginStatus() {
    const isLoggedIn = await sendMessage({ action: 'isLoggedIn' });
    
    if (isLoggedIn) {
        currentUser = await getCurrentUserInfo();
        showLoggedInView();
        await loadUserStatus();
        await loadSlackStatus();
    } else {
        showLoggedOutView();
    }
    
    document.getElementById('loading').classList.add('hidden');
}

function showLoggedOutView() {
    document.getElementById('logged-out-view').classList.remove('hidden');
    document.getElementById('logged-in-view').classList.add('hidden');
}

function showLoggedInView() {
    document.getElementById('logged-out-view').classList.add('hidden');
    document.getElementById('logged-in-view').classList.remove('hidden');
    
    if (currentUser) {
        document.getElementById('user-email').textContent = currentUser.email || currentUser.uid;
    }
}

async function getCurrentUserInfo() {
    return new Promise((resolve) => {
        chrome.runtime.sendMessage({ action: 'getCurrentUser' }, resolve);
    });
}

async function sendMessage(message) {
    return new Promise((resolve) => {
        chrome.runtime.sendMessage(message, resolve);
    });
}

async function loadUserStatus() {
    if (!currentUser) return;
    
    try {
        const response = await sendMessage({
            action: 'getUserStatus',
            firebaseUid: currentUser.uid
        });
        
        if (response.success && response.data.current_track) {
            const track = response.data.current_track;
            document.getElementById('current-track-info').textContent = 
                `${track.artist} - ${track.name}`;
        }
        
        if (response.data.priority && response.data.priority.list) {
            document.getElementById('priority-list').value = response.data.priority.list;
        }
    } catch (error) {
        console.error('Error loading user status:', error);
    }
}

async function loadSlackStatus() {
    if (!currentUser) return;
    
    try {
        const response = await sendMessage({
            action: 'getSlackSyncStatus',
            firebaseUid: currentUser.uid
        });
        
        const statusDiv = document.getElementById('slack-status');
        if (response.running) {
            statusDiv.textContent = `Slack sync active. Current: ${response.current_song || 'No song'}`;
            statusDiv.style.color = '#2e7d32';
        } else {
            statusDiv.textContent = 'Slack sync not running';
            statusDiv.style.color = '#666';
        }
    } catch (error) {
        console.error('Error loading Slack status:', error);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('login-btn').addEventListener('click', async () => {
        const result = await sendMessage({ action: 'login' });
        if (result.success) {
            await checkLoginStatus();
        } else {
            alert('Login failed: ' + result.error);
        }
    });

    document.getElementById('logout-btn').addEventListener('click', async () => {
        await sendMessage({ action: 'logout' });
        showLoggedOutView();
        document.getElementById('loading').classList.add('hidden');
    });

    document.getElementById('update-status-btn').addEventListener('click', async () => {
        if (!currentUser) return;
        
        const name = document.getElementById('track-name').value.trim();
        const artist = document.getElementById('artist-name').value.trim();
        const source = document.getElementById('source').value;
        
        if (!name || !artist) {
            alert('Please enter both track name and artist');
            return;
        }
        
        try {
            const response = await sendMessage({
                action: 'setStatus',
                firebaseUid: currentUser.uid,
                name,
                artist,
                source
            });
            
            if (response.success) {
                alert('Status updated successfully!');
                await loadUserStatus();
                document.getElementById('track-name').value = '';
                document.getElementById('artist-name').value = '';
            } else {
                alert('Failed to update status: ' + response.error);
            }
        } catch (error) {
            alert('Error updating status: ' + error.message);
        }
    });

    document.getElementById('update-priority-btn').addEventListener('click', async () => {
        if (!currentUser) return;
        
        const priorityInput = document.getElementById('priority-list').value.trim();
        if (!priorityInput) {
            alert('Please enter priority list');
            return;
        }
        
        const priorityList = priorityInput.split(',').map(s => s.trim()).filter(Boolean);
        
        try {
            const response = await sendMessage({
                action: 'setPriority',
                firebaseUid: currentUser.uid,
                priorityList
            });
            
            if (response.success) {
                alert('Priority updated successfully!');
            } else {
                alert('Failed to update priority: ' + response.error);
            }
        } catch (error) {
            alert('Error updating priority: ' + error.message);
        }
    });

    document.getElementById('start-slack-sync-btn').addEventListener('click', async () => {
        if (!currentUser) return;
        
        try {
            const response = await sendMessage({
                action: 'startSlackSync',
                firebaseUid: currentUser.uid
            });
            
            if (response.success) {
                alert('Slack sync started!');
                await loadSlackStatus();
            } else {
                alert('Failed to start Slack sync: ' + response.error);
            }
        } catch (error) {
            alert('Error starting Slack sync: ' + error.message);
        }
    });

    document.getElementById('stop-slack-sync-btn').addEventListener('click', async () => {
        if (!currentUser) return;
        
        try {
            const response = await sendMessage({
                action: 'stopSlackSync',
                firebaseUid: currentUser.uid
            });
            
            if (response.success) {
                alert('Slack sync stopped!');
                await loadSlackStatus();
            } else {
                alert('Failed to stop Slack sync: ' + response.error);
            }
        } catch (error) {
            alert('Error stopping Slack sync: ' + error.message);
        }
    });

    checkLoginStatus();
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'getCurrentUser') {
        chrome.runtime.sendMessage({ action: 'getStoredUser' }, (user) => {
            sendResponse(user);
        });
        return true;
    }
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
  
              let title = document.querySelector('.title.style-scope.ytd-video-primary-info-renderer')?.textContent || document.title;
  
              title = title.replace(/^\(\d+\)\s*-\s*/, '');
              title = title.replace(/\s*-\s*YouTube$/, '');
              
              const artist = document.querySelector('#text-container yt-formatted-string.ytd-channel-name')?.textContent || null;
  
              return { title, artist, source: 'youtube' };
            }
          });
          if (result.result) return result.result;
        } else if (tab.url.includes('soundcloud.com')) {
            const [result] = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: () => {
                const playButton = document.querySelector('.playControl.playing');
                if (!playButton) return null;
          
                let title = document.querySelector('.playbackSoundBadge__titleLink')?.textContent || '';
                const artist = document.querySelector('.playbackSoundBadge__lightLink')?.textContent 
                            || document.querySelector('.playbackSoundBadge__userLink')?.textContent 
                            || null;
          
                if (!title || !artist) return null;
          
                function removeDuplicate(str) {
                  const len = str.length;
                  for (let i = 1; i <= len / 2; i++) {
                    const part = str.slice(0, i);
                    if (part.repeat(2) === str) {
                      return part;
                    }
                  }
                  return str;
                }
          
                title = removeDuplicate(title).trim();
          
                return { title, artist, source: 'soundcloud' };
              }
            });
            if (result.result) return result.result;
          }
          
          
  
  
      } catch (e) {
        console.error(`Fehler bei Tab ${tab.url}:`, e);
      }
    }
    return null;
  }
  
  
  
  


document.addEventListener('DOMContentLoaded', () => {
    const artistDiv = document.getElementById('track-artist');
    const titleDiv = document.getElementById('track-title');
    const sourceDiv = document.getElementById('track-source');
  
    async function updateTrack() {
      try {
        const track = await findPlayingTrack();
  
        if (track) {
          artistDiv.textContent = `Artist: ${track.artist || 'Unbekannt'}`;
          titleDiv.textContent = `Title: ${track.title || 'Unbekannt'}`;
          sourceDiv.textContent = `Source: ${track.source || 'Unbekannt'}`;
        } else {
          artistDiv.textContent = 'Artist: -';
          titleDiv.textContent = 'Title: -';
          sourceDiv.textContent = 'Source: -';
        }
      } catch (error) {
        console.error('Fehler beim Track holen:', error);
        artistDiv.textContent = 'Artist: Fehler';
        titleDiv.textContent = 'Title: Fehler';
        sourceDiv.textContent = 'Source: Fehler';
      }
    }
  
    updateTrack();
    setInterval(updateTrack, 5000);
  });
  
  