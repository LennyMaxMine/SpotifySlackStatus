function getNowPlaying() {
    let media = document.querySelector('audio, video');
    if (media && !media.paused) {
      let site = window.location.hostname;
  
      let artist = null;
      let song = null;
  
      const ogArtist = document.querySelector('meta[property="og:music:artist"]');
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogArtist) artist = ogArtist.content;
      if (ogTitle) song = ogTitle.content;
  
      if (!artist) {
        const twArtist = document.querySelector('meta[name="twitter:creator"]');
        if (twArtist) artist = twArtist.content;
      }
      if (!artist) {
        const metaArtist = document.querySelector('meta[name="artist"]');
        if (metaArtist) artist = metaArtist.content;
      }
      if (!artist) {
        const byline = document.querySelector('meta[name="byl"]');
        if (byline) artist = byline.content;
      }
  
      if (!song) {
        let title = document.title;
        if (title.includes(" - ")) {
          [artist, song] = title.split(" - ");
        } else if (title.includes(" • ")) {
          [artist, song] = title.split(" • ");
        } else {
          song = title;
        }
      }
  
      return {
        playing: true,
        artist: artist || "Unknown",
        title: song || "Unknown",
        site
      };
    }
    return { playing: false };
  }
  
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "getNowPlaying") {
      sendResponse(getNowPlaying());
    }
  });
  