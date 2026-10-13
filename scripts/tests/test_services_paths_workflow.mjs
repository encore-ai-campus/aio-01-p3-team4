import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import YAML from 'yaml';

const services=['backend','classifier','locator','verifier','writer','mailer','chaos','frontend'];
for (const service of services) {
  const workflow=YAML.parse(fs.readFileSync(new URL(`../../.github/workflows/${service}.yml`,import.meta.url),'utf8'));
  test(`${service}: service paths are isolated and docs do not trigger deployment`,()=>{
    const paths=workflow.on.push.paths;
    assert.deepEqual(paths,workflow.on.pull_request.paths);
    assert.ok(paths.includes(`src/${service}/**`));
    assert.ok(paths.includes(`.github/workflows/${service}.yml`));
    for(const other of services.filter(value=>value!==service)) {
      assert.ok(!paths.includes(`src/${other}/**`));
      assert.ok(!paths.includes(`.github/workflows/${other}.yml`));
    }
    assert.ok(!paths.some(path=>path.startsWith('docs/') || path==='README.md'));
  });
  test(`${service}: agreed shared dependencies trigger all services`,()=>{
    for(const path of ['contracts/**','database/**','docker-compose.yml','.dockerignore','scripts/deploy_*.sh','scripts/tests/test_deploy_*.sh','scripts/tests/test_*workflow.mjs','.github/actions/**']) {
      assert.ok(workflow.on.push.paths.includes(path),path);
    }
  });
  test(`${service}: selected image build, deploy and main notification are wired`,()=>{
    assert.ok(workflow.jobs.test.steps.some(step=>step.run===`docker compose build ${service}`));
    assert.equal(workflow.jobs.deploy.steps[1].run,`bash scripts/deploy_service.sh ${service}`);
    assert.deepEqual(Object.keys(workflow.jobs.deploy.steps[1].env).sort(),['AWS_HOST','AWS_USER','AWS_SSH_KNOWN_HOSTS','AWS_SSH_PRIVATE_KEY'].sort());
    assert.equal(workflow.jobs.notify.if,"always() && github.event_name == 'push' && github.ref == 'refs/heads/main'");
  });
}
