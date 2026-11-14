// Dados das páginas falsas (estrutura copiada das páginas REAIS de 14/11). ESTADO é injetado pelo servidor.
const JOGOS = {
  "123": {t1: "Phoenix Suns", t2: "Charlotte Hornets", c1: "Phoenix", c2: "Charlotte",
          liga1w: "eua-nba", ligaps: "NBA"},
  "456": {t1: "Atlético de Atenas", t2: "São Lourenço", c1: "Atl. Atenas", c2: "São Lourenço",
          liga1w: "argentina-liga-nacional", ligaps: "Argentina - Liga Nacional"},
  "789": {t1: "Flamengo", t2: "Franca", c1: "Flamengo", c2: "Franca",
          liga1w: "brasil-nbb", ligaps: "Brasil - NBB"},
};
const LIGAS1W = [["eua-nba", "EUA. NBA", ["123"]], ["argentina-liga-nacional", "Argentina. Liga Nacional", ["456"]],
                 ["brasil-nbb", "Brasil. NBB", ["789"]], ["espanha-acb", "Espanha. ACB", []]];
// 1aWin: [título do mercado, cabeçalho de colunas, botões [rótulo, odd]]  (títulos reais: "Totais (inc OT)", "Totais. 2º quarto"…)
function mercados1w(j) {
  return [
    ["Totais (inc OT)", ["Menos", "Mais"], [["Abaixo De 204.5", "2.85"], ["Acima De 204.5", "1.40"], ["Abaixo De 209.5", "2.25"], ["Acima De 209.5", "1.62"],
                   ["Abaixo De 214.5", "2.10"], ["Acima De 214.5", "1.98"], ["Abaixo De 219.5", "1.48"], ["Acima De 219.5", "2.55"]]],
    [j.t1 + " total (inc OT)", ["Menos", "Mais"], [["Abaixo De 105.5", "1.95"], ["Acima De 105.5", "1.85"]]],
    ["Par/Ímpar (inc OT)", [], [["Impar", "1.85"], ["Par", "1.85"]]],
    ["Vencedor Do Ponto", [], [[j.t1 + " 174.0", "1.83"], [j.t2 + " 174.0", "1.87"]]],
    ["Handicap (inc OT)", [j.t1, j.t2], [[j.t1 + " -3.5", "1.90"], [j.t2 + " 3.5", "1.90"], [j.t1 + " -5.5", "2.20"], [j.t2 + " 5.5", "1.65"],
                      [j.t2 + " 9.5", "2.05"]]],
    ["4º quarto. Corrida até N pontos", [], [[j.t1 + " 25", "2.05"], ["Nenhum 25", "5.37"]]],
    ["Margens de vitória (inc OT)", [], [[j.t1 + " 1-2", "9.0"], [j.t2 + " 3-6", "8.5"]]],
    ["Totais. 1º quarto", ["Menos", "Mais"], [["Abaixo De 54.5", "2.20"], ["Acima De 54.5", "1.70"]]],
    ["Totais. 2º quarto", ["Menos", "Mais"], [["Abaixo De 55.5", "1.95"], ["Acima De 55.5", "1.85"]]],
    ["Totais. 4º quarto", ["Menos", "Mais"], [["Abaixo De 58.5", "2.05"], ["Acima De 58.5", "1.80"]]],
    ["Handicap. 4º quarto", [j.t1, j.t2], [[j.t1 + " -1.5", "1.97"], [j.t2 + " 1.5", "1.74"]]],
  ];
}
// PS: âncoras reais <a id="evento|período|mercado|lado|alt|linha">  (mercado 2 = HDP lado 0 casa/1 fora; 3 = OU lado 3 acima/4 abaixo)
// [período, mercado, lado, linha, odd, travado]
function ancorasPS(j) {
  return [
    [[0, 2, 0, -4.5, "2.000"], [0, 2, 1, 4.5, "1.850"], [0, 3, 3, 208.5, "1.550"], [0, 3, 4, 208.5, "2.400"]],
    [[0, 2, 0, -2.5, "1.700"], [0, 2, 1, 2.5, "2.150"], [0, 3, 3, 209.5, "1.620"], [0, 3, 4, 209.5, "2.270"]],
    [[0, 2, 0, -6.5, "2.400", true], [0, 2, 1, 6.5, "1.600", true], [0, 3, 3, 210.5, "1.700"], [0, 3, 4, 210.5, "2.150"]],
    [[0, 3, 3, 211.5, "1.800"], [0, 3, 4, 211.5, "2.020"], [1, 3, 3, 105.5, "1.900"], [1, 3, 4, 105.5, "1.900"]],
    [[0, 3, 3, 212.5, "1.900"], [0, 3, 4, 212.5, "1.920"], [3, 3, 3, 52.5, "2.000"], [3, 3, 4, 52.5, "1.800"]],
    [[4, 3, 3, 55.5, "1.870"], [4, 3, 4, 55.5, "1.930"], [6, 3, 3, 52.5, "2.000"], [6, 3, 4, 52.5, "1.800"]],
  ];
}
function relogio() { return ESTADO.q == 1 ? "1º Quarto 8`" : ESTADO.q == 3 ? "3º Quarto 2`" : "2º Quarto 5`"; }
function relogioPS() { return "Q" + (ESTADO.q || 2) + " 5'"; }
function qAtual() { return ESTADO.q || 2; }
