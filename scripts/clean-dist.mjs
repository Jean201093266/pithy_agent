import fs from 'fs';
import path from 'path';

const distDir = path.join(process.cwd(), 'dist');
if (fs.existsSync(distDir)) {
  fs.rmSync(distDir, { recursive: true, force: true });
  console.log('[clean-dist] removed dist directory');
} else {
  console.log('[clean-dist] dist directory does not exist');
}

