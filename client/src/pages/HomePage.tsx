import { useState } from "react";
import { ArrowDownIcon, ArrowRightIcon, CheckIcon, ChevronDownIcon, FileTextIcon, MicIcon, QuoteIcon, TargetIcon } from "lucide-react";
import { Brand } from "@/components/layout/Brand";
import { Button } from "@/components/ui/button";

const previews = [
  { role: "HR", initials: "HR", title: "Make your story clear.", question: "Walk me through a project you’re proud of. What was your part in it?", note: "Start with your background, motivation, and the choices behind your work.", caption: "Your story, in your own words" },
  { role: "Hiring manager", initials: "HM", title: "Go beyond the job title.", question: "You mentioned leading the launch. What did you own, and what changed because of your decisions?", note: "Explore ownership, collaboration, trade-offs, and the impact of your work.", caption: "A follow-up grounded in your experience" },
  { role: "Specialist", initials: "DS", title: "Explain the work behind the claim.", question: "Choose one decision from that project. What alternatives did you consider, and why did you choose this approach?", note: "Discuss the details of your discipline, whether you work in engineering, sales, or another field.", caption: "Depth that follows your profession" },
];

const faqs = [
  ["Do I need a GitHub repository?", "No. Start with a resume or a few sentences about your experience. A public repository or work sample is optional, and the interview supports technical and nontechnical roles."],
  ["Can I type instead of speaking?", "Yes. Choose typing during setup, or switch to typing during a voice session. Spoken delivery is not assessed in the text lane."],
  ["What happens to my background and answers?", "Before uploading, setup explains which data goes to Groq and Deepgram and asks for your consent. Your private guest key stays in this browser. You can delete your data from the feedback workspace; copies held by providers follow their own policies."],
  ["Is this a hiring assessment?", "This is a practice tool. Scores are experimental coaching indicators, not hiring predictions. Feedback includes evidence and limits, and you can dispute a finding or practise a specific gap."],
];

export function HomePage({ onStart, onHistory }: { onStart: () => void; onHistory: () => void }) {
  const [selected, setSelected] = useState(0);
  const preview = previews[selected];
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <div className="home-page">
      <header className="site-header">
        <div className="site-header-inner">
          <a href="#" aria-label="Shadowtrace home"><Brand /></a>
          <nav aria-label="Main navigation" className="home-nav">
            <button onClick={() => scrollTo("how-it-works")}>How it works</button>
            <button onClick={() => scrollTo("questions")}>Questions</button>
            <button onClick={onHistory}>Your history</button>
          </nav>
          <Button onClick={onStart} className="header-cta">Start practising <ArrowRightIcon /></Button>
        </div>
      </header>

      <main id="main-content" tabIndex={-1} className="outline-none">
        <section className="hero content-width" aria-labelledby="hero-title">
          <div className="hero-copy">
            <p className="eyebrow"><span className="status-dot" /> YOUR EXPERIENCE. YOUR NEXT CHAPTER.</p>
            <h1 id="hero-title">You’ve done<br />the work.<br /><em>Tell the story.</em></h1>
            <p className="hero-description">Turn your experience into answers you can stand behind. Practise an interview that follows your background, then see exactly what to work on.</p>
            <div className="hero-actions">
              <Button size="lg" onClick={onStart}>Prepare my interview <ArrowRightIcon /></Button>
              <button className="text-action" onClick={() => scrollTo("how-it-works")}>Take a look inside <ArrowDownIcon size={16} /></button>
            </div>
            <div className="hero-detail"><span><MicIcon size={15} /> Speak or type</span><span><FileTextIcon size={15} /> Resume or background</span></div>
          </div>
          <div className="hero-product">
            <div className="preview-topline"><span className="eyebrow">A LOOK INSIDE THE INTERVIEW</span><span className="preview-label">Illustrative preview</span></div>
            <div className="interview-preview">
              <div className="preview-window-bar"><Brand compact /><span>Practice room</span><span className="preview-session-label">ONE VOICE AT A TIME</span></div>
              <div className="preview-role-tabs" role="tablist" aria-label="Preview interviewer rounds">
                {previews.map((item, index) => <button key={item.role} type="button" role="tab" id={`preview-tab-${index}`} aria-controls="preview-panel" aria-selected={index === selected} tabIndex={index === selected ? 0 : -1} onClick={() => setSelected(index)} onKeyDown={(event) => { if (["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (selected + (event.key === "ArrowRight" ? 1 : 2)) % 3; setSelected(next); document.getElementById(`preview-tab-${next}`)?.focus(); } }}>{String(index + 1).padStart(2, "0")} <span>{item.role}</span></button>)}
              </div>
              <div className="preview-conversation" role="tabpanel" id="preview-panel" aria-labelledby={`preview-tab-${selected}`}>
                <div className="interviewer-avatar">{preview.initials}<span /></div>
                <p className="preview-round">{preview.role} ROUND</p>
                <h2>{preview.title}</h2>
                <p className="preview-question">“{preview.question}”</p>
                <div className="preview-wave" aria-hidden="true">{Array.from({ length: 35 }, (_, i) => <i key={i} style={{ height: `${8 + ((i * 13 + 7) % 31)}px` }} />)}</div>
                <p className="preview-caption">{preview.caption}</p>
              </div>
            </div>
            <div className="preview-feedback"><span className="feedback-icon"><QuoteIcon size={19} /></span><div><p>Feedback with something to point to.</p><span>Your answer. The reasoning. Your next step.</span></div><span className="feedback-check"><CheckIcon size={15} /></span></div>
          </div>
        </section>

        <section className="role-strip" aria-label="Supported professions"><div className="content-width"><p>Good stories aren’t just for engineers.</p><ul>{["Software", "Hardware", "Sales", "Marketing", "Operations", "Finance"].map((role) => <li key={role}>{role}</li>)}</ul></div></section>

        <section id="how-it-works" className="how-section content-width" aria-labelledby="how-heading">
          <div className="section-intro"><p className="eyebrow">FROM EXPERIENCE TO A BETTER ANSWER</p><h2 id="how-heading">A little preparation.<br /><em>A more useful conversation.</em></h2><p>Bring your experience. Leave with one clear thing to practise next.</p></div>
          <div className="process-list">
            <article><span className="process-number">01</span><div><h3>Start with what you’ve done.</h3><p>Add your background and target role. Review the statements the interviewers will use, and correct anything that needs context.</p></div><FileTextIcon aria-hidden="true" /></article>
            <article><span className="process-number">02</span><div><h3>Have the conversation.</h3><p>Work through HR, hiring manager, and specialist rounds, or focus on one. Follow-ups connect to your experience and answers.</p></div><MicIcon aria-hidden="true" /></article>
            <article><span className="process-number">03</span><div><h3>Find your next good answer.</h3><p>Read feedback alongside your quoted answers. Challenge a finding, practise a specific gap, and compare your attempts.</p></div><TargetIcon aria-hidden="true" /></article>
          </div>
        </section>

        <section className="practice-note content-width">
          <div className="note-visual" aria-hidden="true"><div className="note-paper"><p>YOUR NEXT PRACTICE</p><h3>Make your<br />contribution<br /><em>specific.</em></h3><span>Explain what you owned.<br />Describe a decision.<br />Connect it to the result.</span><span className="note-rule" /></div><span className="note-side-label">ONE GAP. ONE FOCUSED ATTEMPT.</span></div>
          <div className="note-copy"><p className="eyebrow">SMALL STEPS, WITH CONTEXT</p><h2>Don’t just repeat<br />the interview.<br /><em>Work on the answer.</em></h2><p>Targeted practice takes one gap from your feedback and makes it the focus of a short session. Compare the answers and their evidence, including when there isn’t enough evidence to show improvement.</p><Button variant="outline" onClick={onStart}>Find my starting point <ArrowRightIcon /></Button><span className="example-note">The practice note shown here is an example, not a generated assessment.</span></div>
        </section>

        <section id="questions" className="faq-section content-width"><div><p className="eyebrow">A FEW THINGS TO KNOW</p><h2>Before you<br /><em>step inside.</em></h2></div><div className="faq-list">{faqs.map(([question, answer]) => <details key={question}><summary>{question}<ChevronDownIcon size={18} /></summary><p>{answer}</p></details>)}</div></section>

        <section className="closing-section"><div className="content-width"><p className="eyebrow">READY WHEN YOU ARE</p><h2>Your next interview<br />starts with <em>your story.</em></h2><Button size="lg" onClick={onStart}>Let’s practise <ArrowRightIcon /></Button><p>For practice. Feedback is guidance, not a hiring prediction.</p></div></section>
      </main>
      <footer className="site-footer content-width"><Brand /><span>Interview practice, grounded in you.</span><button onClick={onHistory}>Your history <ArrowRightIcon size={14} /></button></footer>
    </div>
  );
}
