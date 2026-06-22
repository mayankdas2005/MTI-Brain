import { apiFetch } from './client';

export interface FeedbackHistoryItem {
  id: string;
  liked: boolean | null;
  comment: string | null;
  question_text: string | null;
  thread_id: string;
  thread_title: string | null;
  created_at: string;
}

export interface FeedbackHistoryPage {
  items: FeedbackHistoryItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export async function listFeedbackHistory(
  page: number,
  perPage = 10,
): Promise<FeedbackHistoryPage> {
  return apiFetch<FeedbackHistoryPage>(
    `/settings/feedback?page=${page}&per_page=${perPage}`,
  );
}
