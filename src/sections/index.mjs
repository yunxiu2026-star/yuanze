// 区块注册表：在页面配置的 sections 里用 type 引用。
// 新增区块：在本目录写一个导出 render(section, site) 的模块，并在这里登记。
import hero from './hero.mjs';
import facts from './facts.mjs';
import categories from './categories.mjs';
import split from './split.mjs';
import logos from './logos.mjs';
import markets from './markets.mjs';
import gallery from './gallery.mjs';
import steps from './steps.mjs';
import cards from './cards.mjs';
import faq from './faq.mjs';
import cta from './cta.mjs';
import contact from './contact.mjs';
import richtext from './richtext.mjs';

export const sections = { hero, facts, categories, split, logos, markets, gallery, steps, cards, faq, cta, contact, richtext };
