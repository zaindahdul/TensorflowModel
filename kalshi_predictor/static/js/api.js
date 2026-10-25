// Calls to the dashboard server. Errors carry the message the server sent.

async function request(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (err) {
    throw new Error("Could not reach the dashboard. Is it still running?");
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.error || "Something went wrong (" + resp.status + ").");
  return body;
}

export function get(path, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== ""));
  return request(path + (query.size ? "?" + query : ""));
}

export function post(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
