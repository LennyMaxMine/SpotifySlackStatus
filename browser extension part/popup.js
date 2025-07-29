async function checkAuth() {
    try {
      const cookie = await getSessionCookie();
      if (!cookie) {
        document.getElementById("status").textContent = "Not logged in";
        return;
      }
  
      const response = await fetch("http://127.0.0.1:1605/api/user", {
        credentials: "include",
        headers: {
          Cookie: `session=${cookie}`
        }
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
  
  function getSessionCookie() {
    return new Promise((resolve) => {
      chrome.cookies.get({ url: "http://127.0.0.1:1605", name: "session" }, (cookie) => {
        resolve(cookie ? cookie.value : null);
      });
    });
  }
  
  document.addEventListener("DOMContentLoaded", () => {
    checkAuth();
  });
  
  document.getElementById("loginBtn").addEventListener("click", () => {
    chrome.windows.create({
      url: "http://127.0.0.1:1605/login",
      type: "popup",
      width: 400,
      height: 600
    }, (window) => {
      const interval = setInterval(async () => {
        chrome.windows.get(window.id, async (win) => {
          if (!win) {
            clearInterval(interval);
            await checkAuth();
          }
        });
      }, 500);
    });
  });
  