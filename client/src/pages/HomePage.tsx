import type { CSSProperties } from "react";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";
import { ArrowRightIcon, ChevronDownIcon, FileTextIcon, MicIcon, TargetIcon } from "lucide-react";
import { Brand } from "@/components/layout/Brand";
import { Button } from "@/components/ui/button";
import { PracticeCompare } from "@/components/home/PracticeCompare";
import { Reveal } from "@/components/home/Reveal";
import { ResumeScrollStory } from "@/components/home/ResumeScrollStory";

const faqs = [
  ["Do I need a GitHub repository?", "No. Start with a resume or a few sentences about your experience. A public repository or work sample is optional, and the interview supports technical and nontechnical roles."],
  ["Can I type instead of speaking?", "Yes. Choose typing during setup, or switch to typing during a voice session. Spoken delivery is not assessed in the text lane."],
  ["What happens to my background and answers?", "Before uploading, setup explains which data goes to Groq and Deepgram and asks for your consent. Your private guest key stays in this browser. You can delete your data from the feedback workspace; copies held by providers follow their own policies."],
  ["Is this a hiring assessment?", "This is a practice tool. Scores are experimental coaching indicators, not hiring predictions. Feedback includes evidence and limits, and you can dispute a finding or practise a specific gap."],
];

const steps = [
  { title: "Start with what you’ve done.", body: "Add your background and target role. Review the statements the interviewers will use, and correct anything that needs context.", Icon: FileTextIcon },
  { title: "Have the conversation.", body: "Work through HR, hiring manager, and specialist rounds, or focus on one. Follow-ups connect to your experience and answers.", Icon: MicIcon },
  { title: "Find your next good answer.", body: "Read feedback alongside your quoted answers. Challenge a finding, practise a specific gap, and compare your attempts.", Icon: TargetIcon },
];

/** Hero entrance delay in ms; the keyframes live in home.css so content shows even if JS animation fails. */
const enter = (ms: number) => ({ "--enter-delay": `${ms}ms` }) as CSSProperties;

export function HomePage({ onStart, onHistory }: { onStart: () => void; onHistory: () => void }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  return (
    // domAnimation loads with the page: the hero story needs it on first paint, and a
    // lazily attached renderer missed the first stage measurement.
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <div className="home-page">
          <header className="site-header">
            <div className="site-header-inner">
              <a href="#" aria-label="Shadowtrace home"><Brand /></a>
              <nav aria-label="Main navigation" className="home-nav">
                <button onClick={() => scrollTo("how-it-works")}>How it works</button>
                <button onClick={() => scrollTo("questions")}>Questions</button>
                <button onClick={onHistory}>Your history</button>
              </nav>
              <Button onClick={onStart} className="header-cta arrow-cta">Start practising <ArrowRightIcon /></Button>
            </div>
          </header>

          <main id="main-content" tabIndex={-1} className="outline-none">
            <ResumeScrollStory nextId="how-heading" copy={<>
              <p className="eyebrow enter" style={enter(0)}><span className="status-dot" /> YOUR EXPERIENCE. YOUR NEXT CHAPTER.</p>
              <h1 id="hero-title">
                <span className="hero-line enter" style={enter(80)}>You’ve done</span>{" "}
                <span className="hero-line enter" style={enter(160)}>the work.</span>{" "}
                <span className="hero-line enter" style={enter(240)}><em>Tell the story.</em></span>
              </h1>
              <p className="hero-description enter" style={enter(360)}>Turn your experience into answers you can stand behind. Practise an interview that follows your background, then see exactly what to work on.</p>
              <div className="hero-actions enter" style={enter(430)}>
                <Button size="lg" className="arrow-cta" onClick={onStart}>Prepare my interview <ArrowRightIcon /></Button>
                <span className="hero-detail"><span><MicIcon size={15} /> Speak or type</span><span><FileTextIcon size={15} /> Resume or background</span></span>
              </div>
            </>} />

            <section className="role-strip" aria-label="Supported professions"><div className="content-width"><p>Good stories aren’t just for engineers.</p><ul>{["Software", "Hardware", "Sales", "Marketing", "Operations", "Finance"].map((role) => <li key={role}>{role}</li>)}</ul></div></section>

            <section id="how-it-works" className="how-section content-width" aria-labelledby="how-heading">
              <Reveal className="section-intro"><p className="eyebrow">FROM EXPERIENCE TO A BETTER ANSWER</p><h2 id="how-heading" tabIndex={-1} className="outline-none">A little preparation.<br /><em>A more useful conversation.</em></h2><p>Bring your experience. Leave with one clear thing to practise next.</p></Reveal>
              <Reveal className="process-list" delay={0.08}>
                {steps.map(({ title, body, Icon }, index) => <article key={title}><span className="process-number">{String(index + 1).padStart(2, "0")}</span><div><h3>{title}</h3><p>{body}</p></div><Icon aria-hidden="true" /></article>)}
              </Reveal>
            </section>

            <Reveal as="section" className="practice-note content-width">
              <PracticeCompare />
              <div className="note-copy"><p className="eyebrow">SMALL STEPS, WITH CONTEXT</p><h2>Don’t just repeat<br />the interview.<br /><em>Work on the answer.</em></h2><p>Targeted practice takes one gap from your feedback and makes it the focus of a short session. Compare the answers and their evidence, including when there isn’t enough evidence to show improvement.</p><Button variant="outline" className="arrow-cta" onClick={onStart}>Find my starting point <ArrowRightIcon /></Button><span className="example-note">The note and rewrite shown here are examples, not a generated assessment. This button opens real interview setup.</span></div>
            </Reveal>

            <section id="questions" className="faq-section content-width">
              <Reveal><p className="eyebrow">A FEW THINGS TO KNOW</p><h2>Before you{" "}<br /><em>step inside.</em></h2></Reveal>
              <Reveal className="faq-list" delay={0.08}>{faqs.map(([question, answer]) => <details key={question}><summary>{question}<ChevronDownIcon size={18} aria-hidden="true" /></summary><p>{answer}</p></details>)}</Reveal>
            </section>

            <section className="closing-section"><Reveal className="content-width"><p className="eyebrow">READY WHEN YOU ARE</p><h2>Your next interview<br />starts with <em>your story.</em></h2><Button size="lg" className="arrow-cta" onClick={onStart}>Let’s practise <ArrowRightIcon /></Button><p>For practice. Feedback is guidance, not a hiring prediction.</p></Reveal></section>
          </main>
          <footer className="site-footer content-width"><Brand /><span>Interview practice, grounded in you.</span><button className="arrow-cta" onClick={onHistory}>Your history <ArrowRightIcon size={14} /></button></footer>
        </div>
      </MotionConfig>
    </LazyMotion>
  );
}
