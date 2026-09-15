const form = document.querySelector("#auth-form");
const error = document.querySelector("#auth-error");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  const isSignup = Boolean(document.querySelector("#name"));
  const payload = {
    email: document.querySelector("#email").value.trim(),
    password: document.querySelector("#password").value,
  };
  if (isSignup) {
    payload.name = document.querySelector("#name").value.trim();
  }
  try {
    const response = await fetch(`/api/auth/${isSignup ? "signup" : "login"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      error.textContent = data.error || "Something went wrong.";
      return;
    }
    window.location.href = "/";
  } catch (requestError) {
    error.textContent =
      "Could not reach Aira. Make sure the Flask server is running.";
  }
});
