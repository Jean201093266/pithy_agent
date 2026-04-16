import { spawnSync } from 'child_process';

const steps = [
  ['npm', ['run', 'clean:dist']],
  ['npm', ['run', 'dist:win']],
  ['npm', ['run', 'release:artifacts']],
  ['npm', ['run', 'release:verify']],
];

for (const [cmd, args] of steps) {
  console.log(`[release-pipeline] running: ${cmd} ${args.join(' ')}`);
  const result = spawnSync(cmd, args, { stdio: 'inherit', shell: true });
  if (result.status !== 0) {
    console.error(`[release-pipeline] failed at step: ${cmd} ${args.join(' ')}`);
    process.exit(result.status ?? 1);
  }
}

console.log('[release-pipeline] release build and verification completed successfully.');

