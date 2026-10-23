// Runs the actual snapshot's expressions, Code nodes and edges with inert HTTP boundaries.
const fs = require('node:fs');
const vm = require('node:vm');
const {workflow, input, responses = {}} = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = Object.fromEntries(workflow.nodes.map(n => [n.name, n]));
const outputs = {};
const visited = [];
const requests = [];
const writes = [];
let items = [{json: {Input: input}}];
let name = 'When Executed by Another Workflow';
function context() {
  return {
    $json: items[0]?.json,
    $input: {first: () => items[0], all: () => items},
    $: key => ({
      get item() {
        if (!outputs[key]?.length) throw Error('unexecuted node: ' + key);
        return outputs[key][0];
      },
      first: () => outputs[key]?.[0], all: () => outputs[key] || [],
    }),
  };
}
function evaluate(value) {
  if (typeof value !== 'string' || !value.startsWith('={{')) return value;
  return vm.runInNewContext('(' + value.slice(3, -2).trim() + ')', context(), {timeout: 1000});
}
let error = null;
try {
  while (name) {
    if (visited.length > 40) throw Error('unexpected cycle');
    visited.push(name);
    const node = nodes[name];
    if (!node) throw Error('missing node: ' + name);
    let branch = 0;
    if (node.type === 'n8n-nodes-base.if') {
      const condition = node.parameters.conditions;
      const matches = condition.conditions.map(c => evaluate(c.leftValue) === c.rightValue);
      branch = (condition.combinator === 'and' ? matches.every(Boolean) : matches.some(Boolean)) ? 0 : 1;
    } else if (node.type === 'n8n-nodes-base.code') {
      items = vm.runInNewContext('(function(){' + node.parameters.jsCode + '\n})()', context(), {timeout: 1000});
    } else if (node.type === 'n8n-nodes-base.httpRequest') {
      const params = node.parameters;
      const request = {
        node: name, method: params.method || 'GET', url: evaluate(params.url),
        query: Object.fromEntries((params.queryParameters?.parameters || []).map(p => [p.name, evaluate(p.value)])),
      };
      if (params.sendBody) request.body = JSON.parse(evaluate(params.body));
      requests.push(request);
      if (request.method !== 'GET') writes.push(request);
      if (!(name in responses)) throw Error('unmocked HTTP boundary: ' + name);
      const response = responses[name];
      if (response?.__error) throw Error(response.__error);
      items = [{json: response}];
    } else if (node.type !== 'n8n-nodes-base.executeWorkflowTrigger') {
      throw Error('unsupported node: ' + node.type);
    }
    outputs[name] = items;
    name = items.length ? workflow.connections[name]?.main?.[branch]?.[0]?.node : null;
  }
} catch (err) {
  error = err.message;
}
process.stdout.write(JSON.stringify({visited, requests, writes, error, outputs}));
