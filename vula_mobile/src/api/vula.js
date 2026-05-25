/**
 * Vula API client — connects mobile thin client to the vula_mind backend.
 * Swap VULA_HOST to your desktop's local IP when on the same WiFi mesh.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

const DEFAULT_HOST = "http://192.168.1.100:7438"; // update to your desktop IP

async function getHost() {
  return (await AsyncStorage.getItem("vula_host")) || DEFAULT_HOST;
}

async function getApiKey() {
  return (await AsyncStorage.getItem("vula_api_key")) || "";
}

async function headers(extra = {}) {
  const apiKey = await getApiKey();
  return {
    "Content-Type": "application/json",
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
    ...extra,
  };
}

export async function checkStatus() {
  const host = await getHost();
  const resp = await fetch(`${host}/status`, { signal: AbortSignal.timeout(5000) });
  return resp.json();
}

export async function askQuestion(tenantId, question) {
  const host = await getHost();
  const resp = await fetch(`${host}/query`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify({ tenant_id: tenantId, question, top_k: 5 }),
    signal: AbortSignal.timeout(60000),
  });
  return resp.json();
}

export async function uploadDocument(tenantId, file) {
  const host = await getHost();
  const apiKey = await getApiKey();
  const fd = new FormData();
  fd.append("tenant_id", tenantId);
  fd.append("file", { uri: file.uri, name: file.name, type: file.mimeType });
  const resp = await fetch(`${host}/ingest`, {
    method: "POST",
    headers: apiKey ? { "X-API-Key": apiKey } : {},
    body: fd,
    signal: AbortSignal.timeout(120000),
  });
  return resp.json();
}

export async function researchCompany(tenantId, url) {
  const host = await getHost();
  const resp = await fetch(`${host}/scrape/company`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify({ tenant_id: tenantId, url }),
    signal: AbortSignal.timeout(30000),
  });
  return resp.json();
}

export async function login(email, password) {
  const host = await getHost();
  const resp = await fetch(`${host}/v1/auth/login`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify({ email, password }),
    signal: AbortSignal.timeout(15000),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function getSession() {
  const raw = await AsyncStorage.getItem("vula_session");
  return raw ? JSON.parse(raw) : null;
}

export async function saveSession(session) {
  await AsyncStorage.setItem("vula_session", JSON.stringify(session));
}

export async function clearSession() {
  await AsyncStorage.removeItem("vula_session");
}

export async function setHost(host) {
  await AsyncStorage.setItem("vula_host", host);
}

export async function setApiKey(key) {
  await AsyncStorage.setItem("vula_api_key", key);
}
