import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import YAML from 'yaml';
import {Parser, Lexer, Evaluator, data} from '@actions/expressions';

for (const service of ['backend','classifier','locator','verifier','writer','mailer','chaos','frontend']) {
const workflow = YAML.parse(fs.readFileSync(new URL(`../../.github/workflows/${service}.yml`, import.meta.url), 'utf8'));
const evaluate = (source, context) => {
  const tree = new Parser(new Lexer(source).lex().tokens, ['needs', 'github'], []).parse();
  return new Evaluator(tree, JSON.parse(JSON.stringify(context), data.reviver)).evaluate().coerceString();
};

test(`${service}: deploy depends on test and reads its environment`, () => {
  assert.deepEqual(workflow.jobs.deploy.needs, ['test']);
  assert.equal(workflow.jobs.deploy.environment, service);
  assert.deepEqual(workflow.jobs.notify.needs, ['test', 'deploy']);
  assert.equal(workflow.jobs.notify.steps[1].uses, './.github/actions/discord-notify');
  assert.equal(workflow.jobs.notify.steps[1].with.stage, '테스트·배포');
});

for (const result of ['success', 'failure', 'cancelled', 'skipped']) {
  for (const [event, ref] of [['push', 'refs/heads/main'], ['push', 'refs/heads/dev'], ['pull_request', 'refs/heads/main']]) {
    test(`${service}: deploy gate: ${result}, ${event}, ${ref}`, () => {
      const actual = evaluate(workflow.jobs.deploy.if, {needs: {test: {result}}, github: {event_name: event, ref}});
      assert.equal(actual, result === 'success' && event === 'push' && ref === 'refs/heads/main' ? 'true' : 'false');
    });
  }
}

const statuses = ['success', 'failure', 'cancelled', 'skipped'];
const expected = [
  ['success', 'failure', 'cancelled', 'failure'],
  ['failure', 'failure', 'failure', 'failure'],
  ['cancelled', 'failure', 'cancelled', 'cancelled'],
  ['failure', 'failure', 'cancelled', 'failure'],
];
const expression = workflow.jobs.notify.steps[1].with.result.slice(3, -2).trim();
for (const [i, testResult] of statuses.entries()) {
  for (const [j, deployResult] of statuses.entries()) {
    test(`${service}: notification: test=${testResult}, deploy=${deployResult}`, () => {
      assert.equal(evaluate(expression, {needs: {test: {result: testResult}, deploy: {result: deployResult}}}), expected[i][j]);
    });
  }
}

}
