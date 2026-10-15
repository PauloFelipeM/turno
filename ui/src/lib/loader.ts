// One request at a time, and never a stale answer: a controller for the panel's loaders (the project panel's
// data, the unit history). The key is what the data is about (a project name, a filter); it is read again
// when the answer arrives, so a response for a key the page has left is dropped by `run` (through `isCurrent`),
// and what was asked for while a request ran is repeated with the latest key afterwards.

export interface SerialLoader<K, M extends string> {
  load(mode: M): Promise<void>;
}

export interface SerialLoaderOptions<K, M extends string> {
  /** The key the page wants now (read on every call, never captured). */
  getKey: () => K;
  /** Fetches for `key` and applies the result only while `isCurrent()` is true. */
  run: (key: K, mode: M, isCurrent: () => boolean) => Promise<void>;
  /** Whether a mode asked for while busy is repeated afterwards (a "more" is not). */
  repeat?: (mode: M) => boolean;
  /** Merges the mode already queued with a new one. */
  combine?: (queued: M, mode: M) => M;
}

export function createSerialLoader<K, M extends string>(o: SerialLoaderOptions<K, M>): SerialLoader<K, M> {
  let busy = false;
  let queued: M | null = null;
  const load = async (mode: M): Promise<void> => {
    if (busy) {
      if (!o.repeat || o.repeat(mode)) queued = queued !== null && o.combine ? o.combine(queued, mode) : mode;
      return;
    }
    busy = true;
    const key = o.getKey();
    try {
      await o.run(key, mode, () => Object.is(o.getKey(), key));
    } finally {
      busy = false;
      const q = queued;
      queued = null;
      if (q !== null) void load(q); // `load` is this same function: the key is read again inside
    }
  };
  return { load };
}
