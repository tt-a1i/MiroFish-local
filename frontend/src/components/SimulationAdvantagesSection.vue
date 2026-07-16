<template>
  <section ref="sectionRef" class="simulation-advantages" aria-labelledby="simulation-advantages-title">
    <div class="simulation-advantages__head">
      <span class="simulation-advantages__head-line" aria-hidden="true"></span>
      <h3 id="simulation-advantages-title" class="simulation-advantages__title">传播推演优势</h3>
      <span class="simulation-advantages__head-line" aria-hidden="true"></span>
    </div>

    <div class="simulation-advantages__shell">
      <article class="advantage-panel advantage-panel--network">
        <div class="advantage-panel__header">
          <div>
            <p class="advantage-panel__kicker">智能体仿真引擎</p>
            <h4 class="advantage-panel__name">超大仿真规模，更贴近真实</h4>
          </div>
          <span class="advantage-panel__meta">双平台并行</span>
        </div>
        <p class="advantage-panel__desc">
          以现实事件为触发点，驱动单位、机构、媒体、意见领袖与公众等多元智能体在复杂社交网络中持续互动。
        </p>

        <div class="network-visual" aria-hidden="true">
          <svg class="network-visual__lines" viewBox="0 0 520 260" fill="none">
            <path ref="networkPathARef" d="M260 130 C188 76 132 52 62 76" />
            <path ref="networkPathBRef" d="M260 130 C342 60 418 52 472 92" />
            <path ref="networkPathCRef" d="M260 130 C168 166 122 218 54 202" />
            <path ref="networkPathDRef" d="M260 130 C356 168 408 212 486 184" />
            <path ref="networkPathERef" d="M260 130 C232 88 278 56 328 34" />
            <path ref="networkPathFRef" d="M260 130 C226 176 282 210 336 232" />
          </svg>

          <div class="network-visual__center">
            <span class="center-node__label">事件种子</span>
          </div>

          <span
            v-for="node in networkNodes"
            ref="networkNodeRefs"
            :key="node.label"
            class="network-node"
            :class="`network-node--${node.tone}`"
            :style="{ left: node.x, top: node.y }"
          >
            <span class="network-node__dot"></span>
            <span class="network-node__label">{{ node.label }}</span>
          </span>

          <div class="platform-lane platform-lane--square">
            <span>信息广场</span>
            <span class="lane-meter"><i></i></span>
          </div>
          <div class="platform-lane platform-lane--community">
            <span>话题社区</span>
            <span class="lane-meter"><i></i></span>
          </div>
        </div>
      </article>

      <article class="advantage-panel advantage-panel--workflow">
        <div class="advantage-panel__header">
          <div>
            <p class="advantage-panel__kicker">一键自动流转</p>
            <h4 class="advantage-panel__name">全程自动，一键出结果</h4>
          </div>
          <span class="advantage-panel__meta">5步闭环</span>
        </div>
        <div ref="workflowRef" class="workflow-rail">
          <div
            v-for="(step, index) in workflowSteps"
            :key="step.title"
            class="workflow-step"
            :class="{ 'workflow-step--last': index === workflowSteps.length - 1 }"
          >
            <span class="workflow-step__index">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="workflow-step__dot"></span>
            <span class="workflow-step__title">{{ step.title }}</span>
            <span class="workflow-step__desc">{{ step.desc }}</span>
          </div>
          <span ref="workflowRunnerRef" class="workflow-runner" aria-hidden="true"></span>
        </div>
      </article>

      <article
        v-for="card in mechanismCards"
        :key="card.title"
        ref="cardRefs"
        class="advantage-card"
        :class="`advantage-card--${card.kind}`"
      >
        <div class="advantage-card__icon" aria-hidden="true">
          <svg v-if="card.kind === 'scene'" viewBox="0 0 24 24">
            <path d="M4 6h16M4 12h10M4 18h16" />
            <path d="M17 10l3 2-3 2" />
          </svg>
          <svg v-else-if="card.kind === 'data'" viewBox="0 0 24 24">
            <path d="M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v4H4zM14 15h6v4h-6z" />
          </svg>
          <svg v-else-if="card.kind === 'cost'" viewBox="0 0 24 24">
            <path d="M4 17c4-8 8-8 12 0" />
            <path d="M8 17c3-5 6-5 9 0" />
            <path d="M17 9h3v3" />
            <path d="M20 9l-6 6" />
          </svg>
          <svg v-else viewBox="0 0 24 24">
            <path d="M5 12h5l3-7 3 14 3-7h3" />
            <path d="M4 20h16" />
          </svg>
        </div>
        <div class="advantage-card__content">
          <h4>{{ card.title }}</h4>
          <p>{{ card.desc }}</p>
        </div>
        <div v-if="card.kind === 'cost'" class="branch-visual" aria-hidden="true">
          <span v-for="item in ['A', 'B', 'C']" :key="item">{{ item }}</span>
        </div>
        <div v-if="card.kind === 'trace'" class="trace-visual" aria-hidden="true">
          <span>行为</span>
          <i></i>
          <span>人设</span>
          <i></i>
          <span>报告</span>
        </div>
      </article>

      <div class="advantage-tags-panel">
        <div class="advantage-tags-panel__header">
          <span>适用场景与能力标签</span>
          <span>来自产品介绍提炼</span>
        </div>
        <div class="advantage-marquee" aria-hidden="true">
          <div ref="upperTrackRef" class="advantage-marquee__track">
            <div ref="upperGroupRef" class="advantage-marquee__group">
              <span v-for="tag in upperTags" :key="`upper-${tag}`">{{ tag }}</span>
            </div>
            <div class="advantage-marquee__group">
              <span v-for="tag in upperTags" :key="`upper-copy-${tag}`">{{ tag }}</span>
            </div>
          </div>
        </div>
        <div class="advantage-marquee advantage-marquee--reverse" aria-hidden="true">
          <div ref="lowerTrackRef" class="advantage-marquee__track">
            <div ref="lowerGroupRef" class="advantage-marquee__group">
              <span v-for="tag in lowerTags" :key="`lower-${tag}`">{{ tag }}</span>
            </div>
            <div class="advantage-marquee__group">
              <span v-for="tag in lowerTags" :key="`lower-copy-${tag}`">{{ tag }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import gsap from 'gsap'

const sectionRef = ref(null)
const workflowRef = ref(null)
const workflowRunnerRef = ref(null)
const networkNodeRefs = ref([])
const cardRefs = ref([])
const upperTrackRef = ref(null)
const upperGroupRef = ref(null)
const lowerTrackRef = ref(null)
const lowerGroupRef = ref(null)
const networkPathARef = ref(null)
const networkPathBRef = ref(null)
const networkPathCRef = ref(null)
const networkPathDRef = ref(null)
const networkPathERef = ref(null)
const networkPathFRef = ref(null)

const workflowSteps = [
  { title: '图谱构建', desc: '事件抽取' },
  { title: '人设生成', desc: '多元智能体' },
  { title: '双平台推演', desc: '传播互动' },
  { title: '报告生成', desc: '工具分析' },
  { title: '深度互动', desc: '追问溯源' }
]

const mechanismCards = [
  {
    kind: 'scene',
    title: '通用全能，不限场景',
    desc: '适配舆情发酵、选题追踪、事件传播预测与大众态度演变，输入主题即可启动推演。'
  },
  {
    kind: 'data',
    title: '数据省心，无需预处理',
    desc: '非结构化资料直接导入，系统自动解析事件背景、实体关系与模拟场景。'
  },
  {
    kind: 'cost',
    title: '超低成本，可高频试错',
    desc: '支持多版本策略反复模拟，通过分支对照快速筛选更稳妥的传播路径。'
  },
  {
    kind: 'trace',
    title: '可交互可追溯，不做盲推演',
    desc: '可与任意智能体对话，也能追问报告依据，把观点迁移、行为轨迹和结论串起来。'
  }
]

const networkNodes = [
  { label: '媒体', x: '10%', y: '28%', tone: 'blue' },
  { label: '机构', x: '70%', y: '14%', tone: 'indigo' },
  { label: '意见领袖', x: '78%', y: '44%', tone: 'blue' },
  { label: '公众', x: '12%', y: '72%', tone: 'cyan' },
  { label: '单位', x: '54%', y: '83%', tone: 'indigo' },
  { label: '平台算法', x: '38%', y: '12%', tone: 'cyan' }
]

const upperTags = ['舆情走向分析', '选题视角追踪', '事件传播预测', '大众态度模拟', '复杂社交网络', '舆论演化']
const lowerTags = ['观点迁移', '发酵路径', '任意智能体对话', '报告智能解读', '依据可追溯', '多元智能体']

let observer = null
let ctx = null
let reduceMotionQuery = null
let marqueeTweens = []

const isReducedMotion = () => reduceMotionQuery?.matches === true

const initMarquee = () => {
  marqueeTweens.forEach((tween) => tween.kill())
  marqueeTweens = []
  if (isReducedMotion()) return

  const pairs = [
    { track: upperTrackRef.value, group: upperGroupRef.value, direction: 1 },
    { track: lowerTrackRef.value, group: lowerGroupRef.value, direction: -1 }
  ]

  pairs.forEach(({ track, group, direction }) => {
    if (!track || !group) return
    const width = group.getBoundingClientRect().width
    if (!width) return
    const wrap = gsap.utils.wrap(-width, 0)
    gsap.set(track, { x: direction > 0 ? -width : 0, force3D: true })
    marqueeTweens.push(
      gsap.to(track, {
        x: `${direction > 0 ? '+=' : '-='}${width}`,
        duration: Math.max(20, width / 34),
        ease: 'none',
        repeat: -1,
        modifiers: {
          x: (value) => `${wrap(parseFloat(value))}px`
        }
      })
    )
  })
}

const playAnimation = () => {
  if (!sectionRef.value || isReducedMotion()) return
  ctx?.revert()
  ctx = gsap.context(() => {
    const nodes = networkNodeRefs.value.filter(Boolean)
    const paths = [
      networkPathARef.value,
      networkPathBRef.value,
      networkPathCRef.value,
      networkPathDRef.value,
      networkPathERef.value,
      networkPathFRef.value
    ].filter(Boolean)

    const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
    timeline
      .from('.simulation-advantages__head-line', { scaleX: 0, duration: 0.5, transformOrigin: '50% 50%' })
      .from('.simulation-advantages__title', { y: 12, opacity: 0, duration: 0.36 }, '<0.08')
      .from('.advantage-panel, .advantage-card, .advantage-tags-panel', {
        y: 24,
        opacity: 0,
        duration: 0.58,
        stagger: 0.07
      }, '<0.06')

    paths.forEach((path) => {
      const length = path.getTotalLength()
      gsap.set(path, { strokeDasharray: length, strokeDashoffset: length })
      gsap.to(path, {
        strokeDashoffset: 0,
        duration: 1.15,
        ease: 'power2.out',
        delay: 0.34
      })
    })

    gsap.from(nodes, {
      scale: 0.62,
      opacity: 0,
      duration: 0.48,
      stagger: 0.08,
      ease: 'back.out(1.7)',
      delay: 0.44,
      transformOrigin: '50% 50%'
    })

    nodes.forEach((node, index) => {
      gsap.to(node, {
        y: index % 2 === 0 ? -7 : 7,
        x: index % 3 === 0 ? 4 : -4,
        duration: 2.6 + index * 0.18,
        repeat: -1,
        yoyo: true,
        ease: 'sine.inOut',
        delay: 1 + index * 0.08
      })
    })

    gsap.to('.network-visual__center', {
      boxShadow: '0 0 0 16px rgba(34, 68, 232, 0)',
      duration: 1.8,
      repeat: -1,
      ease: 'power1.out'
    })

    gsap.to('.lane-meter i', {
      xPercent: 260,
      duration: 2.2,
      repeat: -1,
      ease: 'none',
      stagger: 0.22
    })

    if (workflowRunnerRef.value && workflowRef.value) {
      gsap.to(workflowRunnerRef.value, {
        y: () => Math.max(0, workflowRef.value.offsetHeight - 18),
        duration: 4.2,
        repeat: -1,
        ease: 'power1.inOut',
        repeatDelay: 0.28
      })
    }

    gsap.to('.branch-visual span', {
      opacity: 1,
      y: -3,
      duration: 0.42,
      stagger: 0.28,
      repeat: -1,
      yoyo: true,
      repeatDelay: 0.55,
      ease: 'sine.inOut'
    })

    gsap.to('.trace-visual i', {
      scaleX: 1,
      duration: 0.64,
      stagger: 0.24,
      repeat: -1,
      repeatDelay: 0.8,
      transformOrigin: '0% 50%',
      ease: 'power1.inOut'
    })
  }, sectionRef.value)
}

onMounted(async () => {
  reduceMotionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)') || null
  await nextTick()
  initMarquee()

  if (isReducedMotion()) return
  observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      playAnimation()
      observer?.disconnect()
      observer = null
    },
    { threshold: 0.22 }
  )
  if (sectionRef.value) observer.observe(sectionRef.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  ctx?.revert()
  ctx = null
  marqueeTweens.forEach((tween) => tween.kill())
  marqueeTweens = []
})
</script>

<style scoped>
.simulation-advantages {
  width: 100%;
  max-width: 1120px;
  margin: 64px auto 0;
  color: #1A1A2E;
}

.simulation-advantages__head {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-bottom: 28px;
}

.simulation-advantages__head-line {
  flex: 0 1 160px;
  height: 1px;
}

.simulation-advantages__head-line:first-child {
  background: linear-gradient(90deg, transparent, rgba(135, 146, 166, 0.45));
}

.simulation-advantages__head-line:last-child {
  background: linear-gradient(270deg, transparent, rgba(135, 146, 166, 0.45));
}

.simulation-advantages__title {
  margin: 0;
  flex-shrink: 0;
  color: #8792A6;
  font-size: 24px;
  font-weight: 500;
  line-height: 33px;
  letter-spacing: 0;
}

.simulation-advantages__shell {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.85fr);
  gap: 16px;
}

.advantage-panel,
.advantage-card,
.advantage-tags-panel {
  box-sizing: border-box;
  border: 1px solid rgba(232, 238, 251, 0.95);
  border-radius: 14px;
  background: #FFFFFF;
  box-shadow: 0 6px 28px rgba(15, 35, 71, 0.06);
}

.advantage-panel {
  min-width: 0;
  padding: 22px 24px;
  overflow: hidden;
}

.advantage-panel--network {
  min-height: 360px;
}

.advantage-panel--workflow {
  min-height: 360px;
}

.advantage-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}

.advantage-panel__kicker {
  margin: 0 0 5px;
  color: #8A99B8;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
}

.advantage-panel__name,
.advantage-card h4 {
  margin: 0;
  color: #1A1A2E;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.45;
}

.advantage-panel__meta {
  flex-shrink: 0;
  padding: 4px 9px;
  border: 1px solid #DDE7F6;
  border-radius: 999px;
  background: #F8FBFF;
  color: #315FC8;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
}

.advantage-panel__desc,
.advantage-card p {
  margin: 0;
  color: #667085;
  font-size: 12px;
  line-height: 1.7;
}

.network-visual {
  position: relative;
  height: 244px;
  margin-top: 18px;
  border-radius: 12px;
  background:
    linear-gradient(90deg, rgba(25, 66, 255, 0.04) 1px, transparent 1px),
    linear-gradient(0deg, rgba(25, 66, 255, 0.04) 1px, transparent 1px),
    #F8FBFF;
  background-size: 38px 38px;
  overflow: hidden;
}

.network-visual__lines {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.network-visual__lines path {
  stroke: rgba(34, 68, 232, 0.34);
  stroke-width: 1.8;
  stroke-linecap: round;
}

.network-visual__center {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 86px;
  height: 86px;
  border: 1px solid rgba(34, 68, 232, 0.26);
  border-radius: 999px;
  background: radial-gradient(circle at 50% 45%, #FFFFFF 0%, #F2F7FF 76%);
  box-shadow: 0 0 0 0 rgba(34, 68, 232, 0.18);
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.center-node__label {
  color: #1942FF;
  font-size: 12px;
  font-weight: 800;
}

.network-node {
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid #D9E6FA;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 4px 14px rgba(36, 72, 139, 0.08);
  transform: translate(-50%, -50%);
  white-space: nowrap;
}

.network-node__dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #1942FF;
}

.network-node--cyan .network-node__dot { background: #2BB8D6; }
.network-node--indigo .network-node__dot { background: #6366F1; }

.network-node__label {
  color: #34405A;
  font-size: 11px;
  font-weight: 700;
}

.platform-lane {
  position: absolute;
  left: 18px;
  right: 18px;
  display: grid;
  grid-template-columns: 72px 1fr;
  align-items: center;
  gap: 10px;
  color: #6B7895;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}

.platform-lane--square { bottom: 44px; }
.platform-lane--community { bottom: 18px; }

.lane-meter {
  position: relative;
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: #E4ECFA;
}

.lane-meter i {
  position: absolute;
  left: -36%;
  top: 0;
  width: 36%;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, transparent, #1942FF, transparent);
}

.workflow-rail {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 18px;
  padding-left: 2px;
}

.workflow-rail::before {
  content: '';
  position: absolute;
  left: 23px;
  top: 13px;
  bottom: 13px;
  width: 1px;
  background: #DFE8F6;
}

.workflow-runner {
  position: absolute;
  left: 16px;
  top: 3px;
  width: 15px;
  height: 15px;
  border-radius: 999px;
  background: #1942FF;
  box-shadow: 0 0 0 5px rgba(25, 66, 255, 0.12);
  pointer-events: none;
}

.workflow-step {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 46px 1fr;
  grid-template-areas:
    "index title"
    "dot desc";
  column-gap: 12px;
  min-height: 46px;
  padding: 7px 0;
}

.workflow-step__index {
  grid-area: index;
  color: #B2BDD0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
}

.workflow-step__dot {
  grid-area: dot;
  justify-self: center;
  width: 7px;
  height: 7px;
  margin-top: 6px;
  border-radius: 999px;
  background: #BBD0F3;
}

.workflow-step__title {
  grid-area: title;
  color: #26324A;
  font-size: 13px;
  font-weight: 800;
  line-height: 1;
}

.workflow-step__desc {
  grid-area: desc;
  margin-top: 7px;
  color: #8A96AD;
  font-size: 11px;
  line-height: 1.35;
}

.advantage-card {
  position: relative;
  display: flex;
  gap: 12px;
  min-height: 132px;
  padding: 20px;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}

.advantage-card:hover {
  border-color: rgba(25, 66, 255, 0.28);
  background: #FDFEFF;
  box-shadow: 0 6px 20px rgba(22, 119, 255, 0.09);
}

.advantage-card__icon {
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  border-radius: 10px;
  background: #EFF6FF;
  color: #1942FF;
  display: flex;
  align-items: center;
  justify-content: center;
}

.advantage-card__icon svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.9;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.advantage-card__content {
  position: relative;
  z-index: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.branch-visual {
  position: absolute;
  right: 15px;
  bottom: 12px;
  display: flex;
  gap: 6px;
}

.branch-visual span {
  width: 20px;
  height: 20px;
  border-radius: 999px;
  background: #F2F7FF;
  color: #1942FF;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.45;
}

.trace-visual {
  position: absolute;
  right: 14px;
  bottom: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.trace-visual span {
  color: #315FC8;
  font-size: 10px;
  font-weight: 700;
}

.trace-visual i {
  width: 18px;
  height: 1px;
  background: #9DB7E8;
  transform: scaleX(0.18);
}

.advantage-tags-panel {
  grid-column: 1 / -1;
  padding: 16px 0 18px;
  overflow: hidden;
}

.advantage-tags-panel__header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 0 20px 12px;
  color: #8A96AD;
  font-size: 11px;
  font-weight: 700;
}

.advantage-tags-panel__header span:first-child {
  color: #26324A;
}

.advantage-marquee {
  overflow: hidden;
}

.advantage-marquee + .advantage-marquee {
  margin-top: 10px;
}

.advantage-marquee__track {
  display: flex;
  width: max-content;
  will-change: transform;
}

.advantage-marquee__group {
  display: inline-flex;
  gap: 10px;
  padding-right: 10px;
}

.advantage-marquee__group span {
  display: inline-flex;
  align-items: center;
  height: 30px;
  padding: 0 13px;
  border: 1px solid #DDE7F6;
  border-radius: 999px;
  background: #F8FBFF;
  color: #315FC8;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

@media (max-width: 920px) {
  .simulation-advantages__shell {
    grid-template-columns: 1fr;
  }

  .advantage-panel--network,
  .advantage-panel--workflow {
    min-height: auto;
  }

  .network-visual {
    height: 228px;
  }
}

@media (max-width: 760px) {
  .simulation-advantages {
    margin-top: 56px;
  }

  .simulation-advantages__title {
    font-size: 22px;
    line-height: 30px;
  }

  .simulation-advantages__shell {
    gap: 12px;
  }

  .advantage-panel,
  .advantage-card {
    padding: 18px;
  }

  .advantage-panel__header,
  .advantage-tags-panel__header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .network-visual {
    height: 250px;
  }

  .network-node__label {
    display: none;
  }

  .network-node {
    padding: 7px;
  }

  .advantage-card {
    min-height: 128px;
  }
}

@media (max-width: 520px) {
  .simulation-advantages__head {
    gap: 10px;
  }

  .simulation-advantages__head-line {
    flex-basis: 64px;
  }

  .network-visual {
    height: 220px;
  }

  .platform-lane {
    grid-template-columns: 62px 1fr;
    left: 12px;
    right: 12px;
  }

  .trace-visual,
  .branch-visual {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .advantage-marquee__track,
  .network-node,
  .workflow-runner,
  .lane-meter i,
  .branch-visual span,
  .trace-visual i {
    animation: none !important;
    transition: none !important;
    transform: none !important;
  }

  .workflow-runner {
    display: none;
  }

  .trace-visual i {
    transform: scaleX(1) !important;
  }
}
</style>
