// Checks a running worker: text, an image, the image size cap and the image count limit.
//
//   node scripts/smoke.mjs --url http://localhost:8080      a container started with --rp_serve_api
//   node scripts/smoke.mjs --endpoint <id>                  a RunPod endpoint; needs RUNPOD_API_KEY
//
//   --model <name>       the served model name (default qwen3-vl-32b)
//   --image-url <link>   also send one image as a link, the way the backend will
//   --request <file>     send one job file and print the answer instead of running the checks
//   --image <file>       with --request: the image that replaces the "${IMAGE}" link in the job
//   --timeout <seconds>  for one request, a cold start included (default 1200)

import { readFile } from 'node:fs/promises';
import { extname } from 'node:path';
import { parseArgs } from 'node:util';
import zlib from 'node:zlib';

const { values: options } = parseArgs({
  options: {
    url: { type: 'string' },
    endpoint: { type: 'string' },
    model: { type: 'string', default: 'qwen3-vl-32b' },
    'image-url': { type: 'string' },
    request: { type: 'string' },
    image: { type: 'string' },
    timeout: { type: 'string', default: '1200' },
  },
});

if (Boolean(options.url) === Boolean(options.endpoint)) {
  console.error('Give either --url <local worker> or --endpoint <RunPod endpoint id>.');
  process.exit(2);
}

const headers = { 'Content-Type': 'application/json' };
let base = options.url?.replace(/\/+$/, '');
if (options.endpoint) {
  if (!process.env.RUNPOD_API_KEY) {
    console.error('RUNPOD_API_KEY is not set.');
    process.exit(2);
  }
  base = `https://api.runpod.ai/v2/${encodeURIComponent(options.endpoint)}`;
  headers.Authorization = `Bearer ${process.env.RUNPOD_API_KEY}`;
}

const timeoutMs = Number(options.timeout) * 1000;
const PENDING = new Set(['IN_QUEUE', 'IN_PROGRESS']);

/** An RGB PNG whose left half is red and right half is blue. */
function splitImage(width, height) {
  const stride = width * 3 + 1;
  const raw = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      raw[y * stride + 1 + x * 3 + (x < width / 2 ? 0 : 2)] = 255;
    }
  }
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const frame = Buffer.alloc(8 + body.length);
    frame.writeUInt32BE(data.length, 0);
    body.copy(frame, 4);
    frame.writeUInt32BE(zlib.crc32(body), 4 + body.length);
    return frame;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
  return `data:image/png;base64,${png.toString('base64')}`;
}

const imagePart = (url) => ({ type: 'image_url', image_url: { url } });
const textPart = (text) => ({ type: 'text', text });

/** Runs one job to its end and returns what the worker answered. */
async function run(input) {
  const deadline = Date.now() + timeoutMs;
  const request = (path, init) =>
    fetch(`${base}${path}`, { ...init, headers, signal: AbortSignal.timeout(timeoutMs) }).then(
      async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
        return response.json();
      },
    );

  let job = await request('/runsync', { method: 'POST', body: JSON.stringify({ input }) });
  while (PENDING.has(job.status)) {
    if (Date.now() > deadline) throw new Error(`job ${job.id} is still ${job.status} after ${options.timeout} s`);
    await new Promise((resolve) => setTimeout(resolve, 2000));
    job = await request(`/status/${job.id}`, { method: 'GET' });
  }
  // RunPod ends a job the worker refused as FAILED, with the worker's message in `error`.
  // A local worker ends it as COMPLETED, with an `error` object as its output.
  if (job.status === 'FAILED') return { error: { message: String(job.error ?? 'the job failed') } };
  if (job.status !== 'COMPLETED') {
    throw new Error(`job ${job.id} ended as ${job.status}: ${JSON.stringify(job.error ?? job.output).slice(0, 500)}`);
  }
  // The worker streams its answer, and a job that was not streamed has one item.
  return Array.isArray(job.output) ? job.output[0] : job.output;
}

const chat = (messages, extra = {}) =>
  run({
    openai_route: '/v1/chat/completions',
    openai_input: { model: options.model, messages, temperature: 0, max_tokens: 64, ...extra },
  });

function answerOf(output) {
  if (output?.error) throw new Error(`the worker refused: ${String(output.error.message).slice(0, 500)}`);
  const text = output?.choices?.[0]?.message?.content;
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error(`no text in the answer: ${JSON.stringify(output).slice(0, 300)}`);
  }
  return text.trim();
}

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

const checks = [
  [
    'text',
    async () => {
      const output = await chat([{ role: 'user', content: 'Answer with the single word: ready' }]);
      return answerOf(output);
    },
  ],
  [
    'image, sent inline',
    async () => {
      const output = await chat([
        {
          role: 'user',
          content: [
            imagePart(splitImage(512, 512)),
            textPart('What colour is the left half of this image? Answer with one word.'),
          ],
        },
      ]);
      const text = answerOf(output);
      expect(/red/i.test(text), `expected "red", got "${text}"`);
      return text;
    },
  ],
  [
    'a large image is reduced to the cap',
    async () => {
      // 2048x2048 is 4,096 visual tokens as sent and at most 1,280 after the cap.
      const output = await chat([
        { role: 'user', content: [imagePart(splitImage(2048, 2048)), textPart('Answer with the single word: ok')] },
      ]);
      answerOf(output);
      const tokens = output.usage?.prompt_tokens;
      expect(Number.isInteger(tokens), 'the answer has no usage.prompt_tokens');
      expect(tokens <= 1400, `the image was not reduced: ${tokens} prompt tokens`);
      return `${tokens} prompt tokens`;
    },
  ],
  [
    'a fifth image is refused',
    async () => {
      const image = imagePart(splitImage(64, 64));
      const output = await chat([
        { role: 'user', content: [image, image, image, image, image, textPart('How many images are there?')] },
      ]);
      expect(output?.error, `five images were accepted: ${JSON.stringify(output).slice(0, 200)}`);
      return String(output.error.message).replace(/\s+/g, ' ').slice(0, 120);
    },
  ],
];

if (options['image-url']) {
  checks.push([
    'image, sent as a link',
    async () => {
      const output = await chat(
        [{ role: 'user', content: [imagePart(options['image-url']), textPart('Describe this image in one sentence.')] }],
        { max_tokens: 96 },
      );
      return answerOf(output);
    },
  ]);
}

async function sendRequestFile() {
  const job = JSON.parse(await readFile(options.request, 'utf8'));
  if (options.image) {
    const types = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
    const type = types[extname(options.image).toLowerCase()];
    if (!type) throw new Error('--image takes a .png, .jpg, .jpeg or .webp file');
    const link = `data:${type};base64,${(await readFile(options.image)).toString('base64')}`;
    const replace = (value) => {
      if (Array.isArray(value)) return value.map(replace);
      if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replace(item)]));
      }
      return value === '${IMAGE}' ? link : value;
    };
    job.input = replace(job.input);
  }
  const started = Date.now();
  const output = await run(job.input);
  console.log(answerOf(output));
  console.error(`\n${((Date.now() - started) / 1000).toFixed(1)} s, ${JSON.stringify(output.usage ?? {})}`);
}

if (options.request) {
  try {
    await sendRequestFile();
  } catch (error) {
    console.error(`FAIL  ${error.message}`);
    process.exit(1);
  }
} else {
  let failed = 0;
  for (const [name, check] of checks) {
    const started = Date.now();
    try {
      const detail = await check();
      console.log(`ok    ${name} (${((Date.now() - started) / 1000).toFixed(1)} s): ${detail}`);
    } catch (error) {
      failed += 1;
      console.log(`FAIL  ${name}: ${error.message}`);
    }
  }
  process.exit(failed ? 1 : 0);
}
