async function checkAuth() {
    try {
      const response = await fetch("http://127.0.0.1:8888/api/user", {
        credentials: "include"
      });
      const data = await response.json();
      const status = document.getElementById("status");
      if (data.authenticated) {
        status.textContent = `Logged in as: ${data.user.displayName}`;
      } else {
        status.textContent = "Not logged in";
      }
    } catch (e) {
      console.error("Auth check failed", e);
    }
  }
  
  document.addEventListener("DOMContentLoaded", () => {
    checkAuth();
  });
  
document.getElementById("loginBtn").addEventListener("click", () => {
  chrome.windows.create({
    url: "http://127.0.0.1:8888",
    type: "popup",
    width: 400,
    height: 600
  }, (window) => {
    const interval = setInterval(() => {
      chrome.windows.get(window.id, (win) => {
        if (!win) {
          clearInterval(interval);
          checkAuth()
        }
      });
    }, 500);
  });
});

  