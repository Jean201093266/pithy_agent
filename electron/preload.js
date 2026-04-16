const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('desktopBridge', {
  mode: 'electron',
});

