/**
 * PowerPoint slide export using PptxGenJS (dynamically imported).
 * Builds one slide per conversation: question as headline, chart as hero
 * image, and up to 5 data table rows as a supporting detail block.
 */

import type { Message } from '@/lib/store/threads';

interface SlideOptions {
  threadTitle: string;
  messages: Message[];
  /** Map of conversation_id → PNG data URL (captured from the DOM) */
  chartImages?: Map<string, string>;
}

function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]+`/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '• ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export async function exportAsSlide(options: SlideOptions): Promise<void> {
  const { default: PptxGenJS } = await import('pptxgenjs');
  const pptx = new PptxGenJS();

  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = 'MTI Brain';
  pptx.title = options.threadTitle;

  const BRAND_BLUE = '1B76B8';
  const DARK = '0F1B2D';
  const GRAY = '6B7A99';
  const BG = 'F4F8FD';

  // Group messages into user/assistant pairs
  const pairs: { question: string; answer: Message }[] = [];
  const msgs = options.messages;
  for (let i = 0; i < msgs.length; i++) {
    if (msgs[i].role === 'user' && i + 1 < msgs.length && msgs[i + 1].role === 'assistant') {
      pairs.push({ question: msgs[i].content, answer: msgs[i + 1] });
      i++;
    }
  }

  if (pairs.length === 0) {
    // Fallback: single slide with just the title
    const slide = pptx.addSlide();
    slide.background = { color: BG };
    slide.addText(options.threadTitle, {
      x: 0.5, y: 2, w: '90%', h: 1,
      fontSize: 24, bold: true, color: DARK, align: 'center',
    });
    await pptx.writeFile({ fileName: `${options.threadTitle}.pptx` });
    return;
  }

  // Build one slide per Q&A pair (max 6 slides to keep decks manageable)
  for (const { question, answer } of pairs.slice(0, 6)) {
    const slide = pptx.addSlide();
    slide.background = { color: BG };

    // Brand bar at top
    slide.addShape(pptx.ShapeType.rect, {
      x: 0, y: 0, w: '100%', h: 0.08, fill: { color: BRAND_BLUE },
    });

    // Question headline
    slide.addText(question.length > 120 ? question.slice(0, 117) + '…' : question, {
      x: 0.5, y: 0.25, w: '90%', h: 0.6,
      fontSize: 16, bold: true, color: DARK,
    });

    const chartImg = answer.conversation_id
      ? options.chartImages?.get(answer.conversation_id)
      : undefined;

    if (chartImg) {
      // Chart occupies left 55% of the slide
      slide.addImage({ data: chartImg, x: 0.5, y: 1.0, w: 5.5, h: 3.5 });

      // Answer text on the right
      const plain = stripMarkdown(answer.content).slice(0, 600);
      slide.addText(plain, {
        x: 6.3, y: 1.0, w: 3.2, h: 3.5,
        fontSize: 10, color: GRAY, valign: 'top',
      });
    } else {
      // No chart - full-width text
      const plain = stripMarkdown(answer.content).slice(0, 800);
      slide.addText(plain, {
        x: 0.5, y: 1.0, w: '90%', h: 4.0,
        fontSize: 11, color: DARK, valign: 'top',
      });
    }

    // Footer
    slide.addText('MTI Brain  ·  Confidential', {
      x: 0.5, y: 4.95, w: '90%', h: 0.25,
      fontSize: 8, color: GRAY, align: 'right',
    });
  }

  const safeName = options.threadTitle
    .replace(/[^a-zA-Z0-9 _-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .slice(0, 60) || 'mti-brain-export';

  await pptx.writeFile({ fileName: `${safeName}.pptx` });
}
