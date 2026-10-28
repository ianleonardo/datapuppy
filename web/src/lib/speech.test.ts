import { describe, expect, it } from 'vite-plus/test';
import { blocksToSpeech } from '../features/chat/Blocks';
import { markdownToSpeech } from './speech';

describe('read aloud text', () => {
  it('strips markdown formatting and code', () => {
    const md =
      '## Result\n\n**Revenue** grew in `SG`. See [the report](http://x).\n\n```sql\nSELECT 1\n```\n- one\n- two';
    const out = markdownToSpeech(md);
    expect(out).toContain('Revenue grew in SG');
    expect(out).toContain('See the report');
    expect(out).not.toMatch(/[*`#]|SELECT 1|http/);
    expect(out).toContain('code shown on screen');
  });
  it('speaks prose and findings, only mentions SQL and diagrams', () => {
    const text = blocksToSpeech([
      { type: 'paragraph', content: [{ type: 'text', text: 'Here are the **top** customers.' }] },
      { type: 'sqlCard', props: { sql: 'SELECT * FROM customers' } },
      { type: 'sqlCard', props: { sql: 'SELECT 2' } },
      { type: 'insight', props: { insights: ['Sales rose 12%'], anomalies: ['March dipped'] } },
      { type: 'mermaid', props: { source: 'erDiagram' } },
      { type: 'recommendationChips', props: { chips: [{ label: 'x', prompt: 'y' }] } },
    ] as any);
    expect(text).toContain('Here are the top customers');
    expect(text.match(/SQL query is in the card/g)).toHaveLength(1);
    expect(text).not.toContain('SELECT');
    expect(text).toContain('Key points. Sales rose 12%');
    expect(text).toContain('Worth a closer look. March dipped');
    expect(text).toContain('diagram below');
    expect(text).not.toMatch(/[?!.]\./);
  });
});
