const STORAGE_KEY = 'supermarket.preferred_chains';
const CHANGE_EVENT = 'supermarket:preferred-chains-changed';

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

export function loadPreferredChains(): string[] | null {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    if (!isStringArray(parsed)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function savePreferredChains(chains: string[] | null): void {
  if (chains === null) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(chains));
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function resolvePreferredChains(availableChainKeys: string[]): string[] {
  if (availableChainKeys.length === 0) {
    return [];
  }
  const stored = loadPreferredChains();
  if (!stored || stored.length === 0) {
    return availableChainKeys;
  }
  const selected = availableChainKeys.filter((key) => stored.includes(key));
  return selected.length > 0 ? selected : availableChainKeys;
}

export function subscribePreferredChainsChange(listener: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) {
      listener();
    }
  };
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener('storage', onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener('storage', onStorage);
  };
}
