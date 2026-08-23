(async () => {
  const configUrl = './authoring/player.json';
  let config = null;
  let dynamicRenderHost = null;

  try {
    const response = await fetch(configUrl, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    config = await response.json();
  } catch (error) {
    console.warn(`[Generative MyGO] Player settings skipped: ${error.message}`);
  }

  await import('../assets/index-982c8eaa.js');

  try {
    const { startDynamicRenderHost } = await import('../extensions/dynamic-render/client.js');
    dynamicRenderHost = await startDynamicRenderHost();
  } catch (error) {
    console.warn(`[Generative MyGO] Dynamic render extension skipped: ${error.message}`);
  }

  if (config) {
    window.setTimeout(() => {
      migrateTextSpeed(config).catch((error) => {
        console.warn(`[Generative MyGO] Player settings migration failed: ${error.message}`);
      });
    }, 500);
  }

  window.addEventListener('beforeunload', () => dynamicRenderHost?.destroy(), { once: true });
})();

async function migrateTextSpeed(config) {
  if (!Number.isFinite(config.textSpeed)) return;
  const migrationKey = `generative-mygo:player-settings:${config.storageKey}`;
  const migratedVersion = Number(window.localStorage.getItem(migrationKey) ?? 0);
  if (migratedVersion >= config.version) return;

  const request = indexedDB.open('localforage');
  const database = await new Promise((resolve, reject) => {
    request.addEventListener('success', () => resolve(request.result), { once: true });
    request.addEventListener('error', () => reject(request.error), { once: true });
    request.addEventListener(
      'upgradeneeded',
      () => {
        if (!request.result.objectStoreNames.contains('keyvaluepairs')) {
          request.result.createObjectStore('keyvaluepairs');
        }
      },
      { once: true },
    );
  });

  if (!database.objectStoreNames.contains('keyvaluepairs')) {
    database.close();
    return;
  }

  const transaction = database.transaction('keyvaluepairs', 'readwrite');
  const store = transaction.objectStore('keyvaluepairs');
  const savedUserData = await requestResult(store.get(config.storageKey));

  if (!savedUserData?.optionData) {
    database.close();
    window.setTimeout(() => {
      migrateTextSpeed(config).catch(() => {});
    }, 500);
    return;
  }
  const currentSpeed = Number(savedUserData.optionData.textSpeed);
  let changed = false;
  if (!Number.isFinite(currentSpeed) || currentSpeed <= config.migrateAtOrBelow) {
    savedUserData.optionData.textSpeed = config.textSpeed;
    store.put(savedUserData, config.storageKey);
    changed = true;
  }

  await transactionDone(transaction);
  database.close();
  window.localStorage.setItem(migrationKey, String(config.version));
  if (changed) window.location.reload();
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.addEventListener('success', () => resolve(request.result), { once: true });
    request.addEventListener('error', () => reject(request.error), { once: true });
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.addEventListener('complete', resolve, { once: true });
    transaction.addEventListener('abort', () => reject(transaction.error), { once: true });
    transaction.addEventListener('error', () => reject(transaction.error), { once: true });
  });
}
