// Dados das páginas falsas. ESTADO é injetado pelo servidor de teste.
const JOGOS = {
  "123": {t1: "Phoenix Suns", t2: "Charlotte Hornets", c1: "Phoenix", c2: "Charlotte",
          liga1w: "eua-nba", ligaps: "EUA - NBA", slugps: "eua-nba"},
  "456": {t1: "Atlético de Atenas", t2: "São Lourenço", c1: "Atl. Atenas", c2: "São Lourenço",
          liga1w: "argentina-liga-nacional", ligaps: "Argentina - Liga Nacional", slugps: "argentina-liga-nacional"},
  "789": {t1: "Flamengo", t2: "Franca", c1: "Flamengo", c2: "Franca",
          liga1w: "brasil-nbb", ligaps: "Brasil - NBB", slugps: "brasil-nbb"},
};
const LIGAS1W = [["eua-nba", "EUA. NBA", ["123"]], ["argentina-liga-nacional", "Argentina. Liga Nacional", ["456"]],
                 ["brasil-nbb", "Brasil. NBB", ["789"]], ["espanha-acb", "Espanha. ACB", []]];
// 1aWin: seções (período) -> mercados -> botões [rótulo, odd]
function mercados1w(j) {
  return [
    ["", "Total", [["Acima de (204.5)", "1.40"], ["Abaixo de (204.5)", "2.85"], ["Acima de (209.5)", "1.62"], ["Abaixo de (209.5)", "2.25"],
                   ["Acima de (214.5)", "1.98"], ["Abaixo de (214.5)", "2.10"], ["Acima de (219.5)", "2.55"], ["Abaixo de (219.5)", "1.48"]]],
    ["", "Handicap", [[j.t1 + " (-3.5)", "1.90"], [j.t2 + " (+3.5)", "1.90"], [j.t1 + " (-5.5)", "2.20"], [j.t2 + " (+5.5)", "1.65"],
                      [j.t2 + " (+9.5)", "2.05"]]],
    ["", "Total individual: " + j.t1, [["Acima de (105.5)", "1.85"], ["Abaixo de (105.5)", "1.95"]]],
    ["", "Vencedor do Ponto 50", [[j.t1, "1.85"], [j.t2, "1.95"]]],
    ["", "Dicas de IA", [["Acima de (211.5)", "2.99"]]],
    ["1º Quarto", "Total", [["Acima de (54.5)", "1.70"], ["Abaixo de (54.5)", "2.20"]]],
    ["2º Quarto", "Total", [["Acima de (55.5)", "1.85"], ["Abaixo de (55.5)", "1.95"]]],
    ["4º Quarto", "Total", [["Acima de (58.5)", "1.80"], ["Abaixo de (58.5)", "2.05"]]],
  ];
}
// PS: período -> linhas [HDP, odd t1, odd t2, Pontos, Acima, Abaixo]
function mercadosPS(j) {
  return [
    ["Jogo", [["-4.5", "2.00", "1.85", "208.5", "1.55", "2.40"], ["-2.5", "1.70", "2.15", "209.5", "1.62", "2.27"],
              ["", "", "", "210.5", "1.70", "2.15"], ["", "", "", "211.5", "1.80", "2.02"], ["", "", "", "212.5", "1.90", "1.92"]]],
    ["1º Quarto", [["", "", "", "52.5", "2.00", "1.80"]]],
    ["2º Quarto", [["", "", "", "55.5", "1.87", "1.93"]]],
    ["4º Quarto", [["", "", "", "52.5", "2.00", "1.80"]]],
  ];
}
function relogio() { return ESTADO.q == 1 ? "1º Quarto 8'" : ESTADO.q == 3 ? "3º Quarto 2'" : "2º Quarto 5'"; }
function qAtual() { return ESTADO.q || 2; }
