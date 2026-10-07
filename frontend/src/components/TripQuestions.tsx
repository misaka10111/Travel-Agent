import { useState } from 'react';

export type TripQuestion = {
  field: string;
  question: string;
  options: string[];
};

type Props = {
  questions: TripQuestion[];
  busy: boolean;
  onSubmit: (reply: string) => void;
};

export function TripQuestions({ questions, busy, onSubmit }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const answered = questions.filter((item) => answers[item.field]?.trim());
  return (
    <form
      className="ta-clarify-card ta-trip-questions"
      onSubmit={(event) => {
        event.preventDefault();
        if (busy || !answered.length) return;
        onSubmit(answered.map((item) => `${item.question} ${answers[item.field].trim()}`).join('；'));
      }}
    >
      <div className="ta-clarify-title">补充这些信息，就能继续安排</div>
      <p className="ta-question-hint">已说过的信息会保留。可以选一个答案，也可以直接填写；其他需求仍能在聊天框补充。</p>
      {questions.map((item) => (
        <fieldset key={item.field} className="ta-trip-question" disabled={busy}>
          <legend>{item.question}</legend>
          {item.options.length > 0 && (
            <div className="ta-clarify-options">
              {item.options.map((option) => (
                <button
                  type="button"
                  key={option}
                  aria-pressed={answers[item.field] === option}
                  className={`ta-clarify-option${answers[item.field] === option ? ' active' : ''}`}
                  onClick={() => setAnswers((previous) => ({ ...previous, [item.field]: option }))}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          <input
            aria-label={item.question}
            value={answers[item.field] ?? ''}
            onChange={(event) => setAnswers((previous) => ({ ...previous, [item.field]: event.target.value }))}
            placeholder="也可以自行填写"
          />
        </fieldset>
      ))}
      <button className="ta-clarify-submit" type="submit" disabled={!answered.length || busy}>
        提交补充信息
      </button>
    </form>
  );
}
