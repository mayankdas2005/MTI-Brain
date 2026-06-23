import { apiFetch } from './client';

export interface FeedbackHistoryItem {
  id: string;
  liked: boolean | null;
  comment: string | null;
  question_text: string | null;
  thread_id: string;
  thread_title: string | null;
  feedback_type: string | null;
  last_triggered_at: string | null;
  trigger_count: number;
  created_at: string;
}

export interface FeedbackHistoryPage {
  items: FeedbackHistoryItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface FeedbackPattern {
  topic_key: string;
  count: number;
  liked_count: number;
  disliked_count: number;
  sample_comments: string[];
  suggested_title: string;
  feedback_ids: string[];
}

export async function listFeedbackHistory(
  page: number,
  perPage = 10,
): Promise<FeedbackHistoryPage> {
  return apiFetch<FeedbackHistoryPage>(
    `/settings/feedback?page=${page}&per_page=${perPage}`,
  );
}

export async function getFeedbackPatterns(): Promise<FeedbackPattern[]> {
  return apiFetch<FeedbackPattern[]>('/settings/feedback/patterns');
}
