const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let backend = null;

function startBackend() {
  const python = process.env.PYTHON_EXECUTABLE || 'python';
  const devRoot = path.resolve(__dirname, '..');
  const packagedRoot = path.join(process.resourcesPath, 'app.asar.unpacked');
  const cwd = process.env.PITHY_BACKEND_DIR
    || (app.isPackaged ? packagedRoot : devRoot);
  const entry = process.env.PITHY_BACKEND_ENTRY || path.join(cwd, 'run.py');

  if (!fs.existsSync(entry)) {
    console.error(`Backend entry not found: ${entry}`);
    return;
  }

  backend = spawn(python, [entry], {
    cwd,
    stdio: 'inherit',
    shell: false,
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadURL('http://127.0.0.1:8000');
}

app.whenReady().then(() => {
  startBackend();
  setTimeout(createWindow, 1200);
});

app.on('window-all-closed', () => {
  if (backend && !backend.killed) backend.kill();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend && !backend.killed) backend.kill();
});

