chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "fetchNowPlaying") {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        chrome.tabs.sendMessage(tabs[0].id, { action: "getNowPlaying" }, response => {
          sendResponse(response);
        });
      });
      return true; 
    }
  });
  