import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import linprog, minimize, LinearConstraint
import itertools
from copy import deepcopy

PRECISION = 1e-12


######## UTILS ########
def matrix_init(A):
    A_init = []
    for i in range(A.shape[0]):
        for j in range(A.shape[1]):
            A_init.append(np.random.normal(A[i][j],1))
    return np.array(A_init).reshape(A.shape[0],A.shape[1])

def project_on_feasible(allocation, A, b):
    """
    Project allocation on feasible set
    :param allocation: allocation to project
    :param A: matrix of constraints
    :param b: vector of constraints
    """
    simplex = np.ones_like(allocation).reshape(1, -1)
    eye = np.eye(len(allocation))
    if A is not None:
        A = np.concatenate([A, eye, -eye, simplex, -simplex], axis=0)
        b = np.concatenate(
            [
                b,
                np.ones(len(allocation)),
                np.zeros(len(allocation)),
                np.array([1]),
                np.array([-1]),
            ],
            axis=0,
        )
    else:
        A = np.concatenate([eye, -eye, simplex, -simplex], axis=0)
        b = np.concatenate(
            [
                np.ones(len(allocation)),
                np.zeros(len(allocation)),
                np.array([1]),
                np.array([-1]),
            ],
            axis=0,
        )
    constraints = LinearConstraint(A=A, ub=b)
    x0 = np.ones_like(allocation) / len(allocation)
    fun = lambda x, y: np.linalg.norm(x - y) ** 2
    try:
        results = minimize(fun=fun, x0=x0, args=(allocation), constraints=constraints)
        x = results["x"]
    except ValueError:
        pass
    if np.abs(np.sum(x) - 1) > 1e-5:
        raise "Allocation doesnt sum to 1"
    return x


def get_policy(mu, A, b):
    """
    Find optimal policy
    :param mu: Reward vector
    :param A: if None solve standard bandit problem without any constraints on policy
    :param b: if None solve standard bandit problem without any constraints on policy
    :return:
        - optimal policy
        - aux info from optimizer
    """
    simplex = np.ones_like(mu).reshape(1, -1)
    eye = np.eye(len(mu))
    one = np.array([1])
    if A is not None:
        A = np.concatenate([A, -eye, simplex, -simplex], axis=0)
        b = np.concatenate([b, np.zeros(len(mu)), one, -one], axis=0)
    else:
        A = np.concatenate([-eye, simplex, -simplex], axis=0)
        b = np.concatenate([np.zeros(len(mu)), one, -one], axis=0)
    try:
        results = linprog(
            -mu, A_ub=A, b_ub=b, A_eq=None, b_eq=None, method="highs-ds"
        )  # Use simplex method
    except ValueError:
        pass
    #print(results)
    #if not results["success"]:
    #    raise "LP Solver failed"
    # Get active constraints
    aux = {"A": A, "b": b, "slack": results["slack"]}
    return results,aux


def arreqclose_in_list(myarr, list_arrays):
    """
    Test if np array is in list of np arrays
    """
    return next(
        (
            True
            for elem in list_arrays
            if elem.size == myarr.size and np.allclose(elem, myarr)
        ),
        False,
    )


def enumerate_all_policies(A, b):
    """
    Enumerate all policies in the polytope Ax <= b
    """
    # Compute all possible bases
    n_constraints = A.shape[0]
    n_arms = A.shape[1]
    bases = list(itertools.combinations(range(n_constraints), n_arms))  #Takes all possible sub-matrices
    policies = []
    for base in bases:
        base = np.array(base)
        B = A[base]
        # Check that the base is not degenerate
        if np.linalg.matrix_rank(B) == A.shape[1]:
            policy = np.linalg.solve(B, b[base])
            # Verify that policy is in the polytope
            if np.all(A.dot(policy) <= b + 1e-5) and not arreqclose_in_list(
                policy, policies
            ):
                policies.append(policy)
    return policies


def compute_neighbors(vertex, A, b, slack):
    """
    Compute all neighbors of vertex in the polytope Ax <= b
    :param vertex: vertex of the polytope
    :param A: matrix of constraints
    :param b: vector of constraints
    :param slack: vector of slack variables
    """
    active = slack == 0
    not_active = slack != 0
    #print(A)
    n_constraints = np.arange(A.shape[0])
    #print(active)
    #print(n_constraints)
    active_constaints = n_constraints[active].tolist()
    inactive_constraints = n_constraints[not_active].tolist()
    neighbors = []

    # Compute all possible bases at the vertex
    bases = list(itertools.combinations(active_constaints, len(vertex)))
    # For each possible base swap one element with an inactive constraint to get a neighbor
    for base in bases:
        for constraint in inactive_constraints:
            # Swap constraint into each position of the base
            for i in range(len(base)):
                new_base = np.array(deepcopy(base))
                new_base[i] = constraint
                B = A[new_base]
                # Check that the base is not degenerate
                if np.linalg.matrix_rank(B) == len(vertex):
                    possible_neighbor = np.linalg.solve(B, b[new_base])
                    # Verify that neighbor is in the polytope
                    if np.all(
                        A.dot(possible_neighbor) <= b + 1e-5
                    ) and not arreqclose_in_list(possible_neighbor, neighbors):
                        neighbors.append(possible_neighbor)
    return neighbors


def binary_search(mu, interval, threshold, kl):
    """
    Find maximizer of KL(mu, x) in interval satysfiyng threshold using binary search
    :param mu: reward of arm
    :param interval: interval to search in
    :param threshold: threshold to satisfy (f(t) = log t)
    :param kl: KL divergence function

    """
    p = 0
    q = len(interval)
    done = False
    while not done:
        i = int((p + q) / 2)
        x = interval[i]
        loss = kl(mu, x)
        if loss < threshold:
            p = i
        else:
            q = i
        if p + 1 >= q:
            done = True

    return x, loss


def get_confidence_interval(mu, pulls, f_t, upper=6, lower=-1, kl=None):
    """
    Compute confidence interval for each arm
    :param mu: reward vector
    :param pulls: number of pulls for each arm
    :param f_t: threshold function f(t) = log t
    :param upper: upper bound for search
    :param lower: lower bound for search
    :param kl: KL divergence function
    :return:
        - lower bound for each arm
        - upper bound for each arm
    """
    if kl is None:
        kl = lambda m1, m2: ((m1 - m2) ** 2) / (2)
    ub = [
        binary_search(m, np.linspace(m, upper, 5000), threshold=f_t / n, kl=kl)[0]
        for m, n in zip(mu, pulls)
    ]
    lb = [
        binary_search(m, np.linspace(m, lower, 5000), threshold=f_t / n, kl=kl)[0]
        for m, n in zip(mu, pulls)
    ]

    return lb, ub



def gaussian_projection(w, mu, pi1, pi2, l0, A, b, sigma=1):
    """
    perform close-form projection onto the hyperplane lambda^T(pi1 - pi2) = 0 assuming Gaussian distribution
    :param w: weight vector
    :param mu: reward vector
    :param pi1: optimal policy
    :param pi2: suboptimal neighbor policy
    :param sigma: standard deviation of Gaussian distribution

    return:
        - lambda: projection
        - value of the projection

    """
    v = pi1 - pi2
    normalizer = ((v**2) / (w + PRECISION)).sum()
    lagrange = mu.dot(v) / (normalizer+PRECISION) 
    lam = mu - lagrange * v / (w + PRECISION)
    var = sigma**2
    value = (w * ((mu - lam) ** 2)).sum() / (2 * var) 
    def objective_for_l(x):
        f_x = (w * ((mu - lam) ** 2)).sum() / (2 * var) + x.dot(b-A.dot(w))
        return f_x
    gamma = A.dot(w)-b
    const = LinearConstraint(np.array([1]*A.shape[0]),lb=0,ub=value/(np.min(gamma)+PRECISION))
    bound = [(0,np.inf) for _ in range(A.shape[0])]
    res = minimize(objective_for_l, x0 = l0, constraints=const, bounds=bound)
    #print(res.x)
    return lam, objective_for_l(res.x), res.x  


def bernoulli_projection(w, mu, pi1, pi2, sigma=1):
    """
    Projection onto the hyperplane lambda^T(pi1 - pi2) = 0 assuming Bernoulli distribution using scipy minimize
    """
    mu = np.clip(mu, 1e-3, 1 - 1e-3)
    bounds = [(1e-3, 1 - 1e-3) for _ in range(len(mu))]
    v = pi1 - pi2
    constraint = LinearConstraint(v.reshape(1, -1), 0, 0)

    def objective(lam):
        kl_bernoulli = mu * np.log(mu / lam) + (1 - mu) * np.log((1 - mu) / (1 - lam))
        return (w * kl_bernoulli).sum()

    x0 = gaussian_projection(w, mu, pi1, pi2, sigma)[0]
    x0 = np.clip(x0, 1e-3, 1 - 1e-3)
    res = minimize(objective, x0, constraints=constraint, bounds=bounds)
    lam = res.x
    value = objective(lam)
    #print(value)
    return lam, value


def best_response(w, mu, pi, neighbors, l0, A, b, sigma=1, dist_type="Gaussian"):
    """
    Compute best response instance w.r.t. w by projecting onto neighbors
    :param w: weight vector
    :param mu: reward vector
    :param pi: optimal policy
    :param neighbors: list of neighbors
    :param sigma: standard deviation of Gaussian distribution
    :param dist_type: distribution type to use for projection

    return:
        - value of best response
        - best response instance
    """
    if dist_type == "Gaussian":
        projections = [
            gaussian_projection(w, mu, pi, neighbor, l0, A, b, sigma) for neighbor in neighbors
        ]
    elif dist_type == "Bernoulli":
        projections = [
            bernoulli_projection(w, mu, pi, neighbor, sigma) for neighbor in neighbors
        ]
    else:
        raise NotImplementedError
    
    values = [p[1] for p in projections]
    instances = [p[0] for p in projections]
    ls = [p[2] for p in projections]
    #print(values)
    return np.min(values), instances[np.argmin(values)], ls[np.argmin(values)]
    


def solve_game(
    mu,
    vertex,
    neighbors,
    l0, 
    A, 
    b,
    sigma=1,
    dist_type="Gaussian",
    allocation_A=None,
    allocation_b=None,
    tol=None,
    x0=None,
):
    """
    Solve the game instance w.r.t. reward vector mu. Used for track-n-stop algorithms
    :param mu: reward vector
    :param vertex: vertex of the game
    :param neighbors: list of neighbors
    :param sigma: standard deviation of Gaussian distribution
    :param dist_type: distribution type to use for projection
    :param allocation_A: allocation constraint. If None allocations lies in simplex.
    :param tol: Default None for speed in TnS.
    :param x0: initial point
    """

    def game_objective(w):
        return -best_response(w, mu, vertex, neighbors, l0, A, b, sigma, dist_type)[0]

    tol_sweep = [1e-16, 1e-12, 1e-6, 1e-4]  # Avoid tolerance issues in scipy
    #print(tol_sweep)
    if tol is not None and tol > tol_sweep[0]:
        tol_sweep = [tol] + tol_sweep
    else:
        tol_sweep = [None] + tol_sweep  # Auto tune tol via None
    if allocation_A is None:
        # Solve optimization problem over simplex
        simplex = np.ones_like(mu).reshape(1, -1)
        constraint = LinearConstraint(A=simplex, lb=1, ub=1)
        bounds = [(0, 1) for _ in range(len(mu))]
        count = 0
        done = False
        if x0 is None:
            while count < len(tol_sweep) and not done:
                x0 = np.random.uniform(0.3, 0.6, size=len(mu))
                x0 = x0 / x0.sum()
                tol = tol_sweep[count]
                res = minimize(
                    game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
                )
                done = res["success"]
                count += 1
        else:
            res = minimize(
                game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
            )
    else:
        # Solve optimization problem over allocation constraint
        constraint = LinearConstraint(A=allocation_A, ub=allocation_b)
        bounds = [(0, 1) for _ in range(len(mu))]
        count = 0
        done = False
        if x0 is None:
            while count < len(tol_sweep) and not done:
                tol = tol_sweep[count]
                #print(tol_sweep)
                #print(1)
                x0 = np.random.uniform(0.3, 0.6, size=len(mu))
                x0 = project_on_feasible(x0, allocation_A, allocation_b)
                #print(x0)
                res = minimize(
                    game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
                )
                #print(res)
                done = res["success"]
                count += 1
        else:
            res = minimize(
                game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
            )
    if res["success"] == True:
        return res.x, -res.fun  #, best_response(res.x, mu, vertex, neighbors, l0, A, b, sigma, dist_type)[2]
    

####### ALGORITHMS ####### 


class Explorer:
    """
    Abstract class for an explorer
    """

    def __init__(
        self,
        n_arms,
        A_init,
        b,
        delta,
        l0,
        ini_phase=1,
        sigma=1,
        restricted_exploration=True,
        dist_type="Gaussian",
        seed=None,
        d_tracking=False,
    ):
        """
        Initialize the explorer
        :param n_arms: number of arms
        :param A: matrix constraints
        :param b: vector constraints
        :param delta: confidence parameter
        :param ini_phase: initial phase (how many times to play each arm before adaptive search starts). Default: 1
        :param sigma: standard deviation of Gaussian distribution
        :param restricted_exploration: whether to use restricted exploration or not
        :param dist_type: distribution type to use for projection
        :param seed: random seed
        """
        self.n_arms = n_arms
        self.A_init = A_init
        self.b = b
        self.delta = delta
        self.ini_phase = ini_phase
        self.sigma = sigma
        self.restricted_exploration = restricted_exploration
        self.dist_type = dist_type
        self.seed = seed
        self.random_state = np.random.RandomState(seed)
        self.d_tracking = d_tracking
        self.cumulative_weights = np.zeros(n_arms)
        self.D = 1
        self.alpha = 1
        self.l0 = l0

        self.t = 0
        self.neighbors = {}
        self.means = np.zeros(n_arms)
        self.constraints = A_init
        self.n_pulls = np.zeros(n_arms)
        self.res = []
        self.au = []
        self.gram_mat = np.eye(n_arms)

        if dist_type == "Gaussian":
            # Set KL divergence and lower/upper bounds for binary search
            self.kl = lambda x, y: 1 / (2 * (sigma**2)) * ((x - y) ** 2)
            self.lower = -1
            self.upper = 10
        elif dist_type == "Bernoulli":
            # Set KL divergence for Bernoulli distribution and lower/upper bounds for binary search
            self.kl = lambda x, y: x * np.log(x / y) + (1 - x) * np.log(
                (1 - x) / (1 - y)
            )
            self.lower = 0 + 1e-4
            self.upper = 1 - 1e-4
            self.ini_phase = (
                10  # Take longer initial phase for Bernoulli to avoid all 0  or all 1
            )
        else:
            raise NotImplementedError

        if restricted_exploration:
            # Compute allocation constraint
            test = np.ones_like(self.means)
            _,aux = get_policy(test, A=self.constraints, b=self.b)
            aux = {"A": self.constraints, "b": aux["b"], "slack": aux["slack"]} 
            self.allocation_A = aux["A"]
            self.allocation_b = aux["b"]
        else:
            self.allocation_A = None
            self.allocation_b = None

    def tracking(self, allocation):
        """
        Output arm based on either d-tracking or cumulative tracking
        """
        if self.d_tracking:
            return np.argmin(self.n_pulls - self.t * allocation)
        else:
            eps = 1 / (2 * np.sqrt(self.t + self.n_arms**2))
            eps_allocation = allocation + eps
            eps_allocation = eps_allocation / eps_allocation.sum()
            self.cumulative_weights += eps_allocation
            return np.argmin(self.n_pulls - self.cumulative_weights)

    def act():
        """
        Choose an arm to play
        """
        raise NotImplementedError

    def stopping_criterion(self, vertex, arm, f_t, w_t_norm):
        """
        Check stopping criterion. Stopping based on the generalized log-likelihood ratio test
        """

        hash_tuple = tuple(vertex.tolist())
        game_value, _, best_l = best_response(
            w=self.empirical_allocation(),
            mu=self.means,
            pi=vertex,
            neighbors=self.neighbors[hash_tuple],
            l0 = self.l0,
            A = self.constraints,
            b = self.b,
            sigma=self.sigma,
            dist_type=self.dist_type,
        )
        #print(best_l)
        #print(self.gram_mat)
        beta = np.log((1 + np.log(self.t))*2 / self.delta)
        #print(beta)

        #print(f)
        return self.t * game_value + best_l.dot(self.b-(self.constraints-f_t*np.sqrt(w_t_norm)).dot(vertex)) > beta + (best_l.sum())*(f_t*np.sqrt(w_t_norm)+1)


    def empirical_allocation(self):
        """
        Compute empirical allocation
        """
        return self.n_pulls / self.t

    def update(self, arm, reward, cost):     # Update empirical estimates
        """
        Update the explorer with the reward obtained from playing the arm
        :param arm: arm played
        :param reward: reward obtained
        """
        self.noise = 1
        self.t += 1
        #print(self.t)
        self.n_pulls[arm] += 1
        self.means[arm] = self.means[arm] + (1 / self.n_pulls[arm]) * (
            reward - self.means[arm]
        )
        self.constraints.T[arm] = ((self.n_pulls[arm]-1)*self.constraints.T[arm] + cost)/self.n_pulls[arm] 
        self.gram_mat += np.outer(np.eye(A.shape[1])[arm],np.eye(A.shape[1])[arm])
        #print(self.constraints)
        
        if self.dist_type == "Bernoulli":
            self.means = np.clip(self.means, self.lower, self.upper)



class CGE(Explorer):
    """
    Constrainted Game Explorer for bandits with linear constraints.

    Performs exploration by treating the lower bound as a zero-sum game.

    Allocation player is AdaHedge
    Instance player performs a best response w.r.t. the allocation

    """

    def __init__(
        self,
        n_arms,
        A_init,
        b,
        delta,
        l0,
        ini_phase=1,
        sigma=1,
        restricted_exploration=False,
        dist_type="Gaussian",
        seed=None,
        d_tracking=True,
        use_adahedge=True,
    ):
        super().__init__(
            n_arms,
            A_init,
            b,
            delta,
            l0,
            ini_phase,
            sigma,
            restricted_exploration,
            dist_type,
            seed,
            d_tracking,
        )
        # Initialize the allocation player
        w = 2*len(self.means)*np.log(1+1/(len(self.means)+self.t))
            #print(np.sqrt(w_t_norm))
        f_t = 1 + np.sqrt((1/2) * np.log(2*len(self.means)/self.delta 
                                       + (len(self.means)/4) * np.log(1 + self.t/len(self.means))))
        if use_adahedge:
            simplex = np.ones_like(self.means).reshape(1, -1)
            eye = np.eye(len(self.means))
            one = np.array([1])
            if restricted_exploration:
                allocation_A = np.concatenate([self.constraints- f_t*np.sqrt(w) , -eye, simplex, -simplex], axis=0)
                allocation_b = np.concatenate([self.b, np.zeros(n_arms), one, -one], axis=0)
                self.ada =  AdaGrad(A=allocation_A, b=allocation_b, loss_rescale=0.01)
            else:
                allocation_A = np.concatenate([self.constraints - f_t*np.sqrt(w), -eye, simplex, -simplex], axis=0)
                allocation_b = np.concatenate([self.b, np.zeros(n_arms), one, -one], axis=0)
                self.ada = AdaGrad(A=allocation_A, b=allocation_b, loss_rescale=0.01)
                #AdaHedge(
                    #A=allocation_A, b=allocation_b, loss_rescale=1
                #)  
        else:
            self.ada = OnlineGradientDescent(d=n_arms)

    def act(self):
        """
        Choose an arm to play
        """
        
        if self.t < self.n_arms * self.ini_phase:
            # Initial phase play each arm once
            arm = self.t % self.n_arms
            return arm, False, None, None
        # Compute optimal policy w.r.t. current empirical means
        
        f_t = 1 + np.sqrt((1/2) * np.log(2*len(self.means)/self.delta 
                                       + (len(self.means)/4) * np.log(1 + self.t/len(self.means)))) 
        w = 2*len(self.means)*np.log(1+1/(len(self.means)+self.t))       
        results,aux= get_policy(mu=self.means, A = self.constraints , b=self.b)        
        optimal_policy = results["x"]
        #print(optimal_policy)
        
        
        if results["success"] == True:
        # Check if policy already visited. If yes retrieve neighbors otherwise compute neighbors
            w_t_norm = np.matmul(np.matmul(optimal_policy, np.linalg.inv(self.gram_mat)),optimal_policy)
            hash_tuple = tuple(optimal_policy.tolist())  # npy not hashable
            if hash_tuple in self.neighbors:
                neighbors = self.neighbors[hash_tuple]
            else:
                neighbors = compute_neighbors(
                    optimal_policy, aux["A"], aux["b"], slack=aux["slack"]
                )
                self.neighbors[hash_tuple] = neighbors

            # Get allocation from AdaHedge
            allocation = self.ada.get_weights()
            #print(allocation)
            # Project allocation on feasible set
            if self.restricted_exploration:
                allocation = project_on_feasible(allocation, self.constraints-f_t*np.sqrt(w_t_norm), self.b)
            #print(allocation)

            # Perform best response
            br_value, br_instance, best_l = best_response(
                w=allocation,
                mu=self.means,
                pi=optimal_policy,
                neighbors=neighbors,
                l0 = self.l0,
                A = self.constraints-f_t*np.sqrt(w_t_norm),
                b = self.b,
                sigma=self.sigma,
                dist_type=self.dist_type,
            )
            #print(f"At time {self.t} constraint estimate is {self.constraints-f_t*np.sqrt(w_t_norm)}")
            # Compute loss for allocation player
            ft = np.log(self.t)
            # Optimism
            lb, ub = get_confidence_interval(
                self.means, self.n_pulls, ft, kl=self.kl, lower=self.lower, upper=self.upper
            )
            loss = [
                np.max(
                    [
                        ft / self.n_pulls[a],
                        self.kl(lb[a], br_instance[a]),
                        self.kl(ub[a], br_instance[a]),
                    ]
                )
                for a in range(self.n_arms)
            ]

            #w_t_norm = np.matmul(np.matmul(optimal_policy, np.linalg.inv(self.gram_mat)),optimal_policy)
            #print(np.sqrt(w_t_norm))
            #f = 1 + np.sqrt((1/2) * np.log(2*len(self.means)/self.delta 
            #                           + (len(self.means)/4) * np.log(1 + self.t/len(self.means))))
            #lag_loss = []
            #for a in range(len(self.means)):
            #    lag_loss.append(best_l.dot(self.b - self.constraints.T[a]*optimal_policy[a]))#+ f*w_t_norm*np.array([1]*len(self.b))))
            
            #x = np.array([self.b-self.constraints.T[i] for i in range(len(self.means))])
            #lag_loss = best_l.dot(x.T)
            # Update allocation player
            self.ada.update(-(np.array(loss)+best_l.dot(self.constraints-f_t*np.sqrt(w_t_norm)))) #+ np.array(lag_loss)))

            not_saturated = self.n_pulls < (np.sqrt(self.t) - self.n_arms / 2)
            if not_saturated.any() and self.d_tracking:
                # Play smallest N below sqrt(t) - n_arms/2
                arm = np.argmin(self.n_pulls)

            else:
                # Play arm according to tracking rule
                arm = self.tracking(allocation)

            # Check stopping criterion
            stop = self.stopping_criterion(optimal_policy,arm,f_t,w_t_norm)

            misc = {
                "br_value": br_value,
                "br_instance": br_instance,
                "allocation": allocation,
                "optimal_policy": optimal_policy,
            }

            return arm, stop, optimal_policy, misc

from scipy.special import softmax

class AdaHedge:
    """
    AdaHedge algorithm from https://arxiv.org/pdf/1301.0534.pdf
    """

    def __init__(self, d, loss_rescale=1):
        """

        :param d: number of arms
        :param loss_rescale: rescale loss to avoid numerical issues
        """
        self.alpha = 4  # 4 #np.sqrt(np.log(d))
        self.w = np.ones(d) / d
        self.theta = np.zeros(d)
        self.t = 0
        self.gamma = 1e-5
        self.loss_rescale = loss_rescale

    def random_weights(self):
        w = np.random.uniform(1, 2, size=len(self.w))
        self.w = w / np.sum(w)

    def get_weights(self):
        """
        Get weights
        """
        return self.w

    def update(self, loss):
        """
        Update weights in AdaHedge, see https://parameterfree.com/2020/05/03/adahedge/
        :param loss:
        :return:
        """
        self.t += 1
        loss = loss * self.loss_rescale
        self.theta = self.theta - loss
        total_loss = (self.w * loss).sum()
        #print(total_loss)
        if self.t == 1:
            delta = total_loss - loss.min() + 1e-5
        else:
            #print(loss)
            print("gamma-",self.gamma)
            #print(self.w)
            #print(np.sum(self.w * np.exp(PRECISION+(-loss / self.gamma))))
            delta = self.gamma * np.log(np.sum(self.w * np.exp((-loss / max(self.gamma,PRECISION))))) + total_loss

        print("delta-",delta)
        
        self.gamma += delta / (self.alpha**2)
        logits = self.theta / self.gamma
        self.w = softmax(logits - logits.max())
        
        
from scipy.linalg import sqrtm


class AdaGrad:
    """
    AdaGrad Algorithm
    """

    def __init__(self, A , b, loss_rescale=1):
        self.eta = 1 / np.sqrt(2)
        self.d = A.shape[1]
        self.t = 0
        self.loss_rescale = loss_rescale
        self.w = np.ones(self.d) / self.d
        self.H = 0
        self.A = A
        self.b = b
        self.w = np.clip(project_on_feasible(self.w, A, b), 1e-12, 1 - 1e-12)
        self.loss_sequence = 0
        self.neg_entropy = lambda x: (x * np.log(x + PRECISION)).sum()
        self.delta = 0.01

    def get_weights(self):
        """
        Get weights
        """
        return self.w

    def update(self, loss):
        self.t += 1
        loss = loss * self.loss_rescale
        self.loss_sequence += loss
        self.H = self.H + np.outer(loss, loss)
        H = self.H + self.delta * np.eye(self.d)
        H_inv = np.linalg.pinv(sqrtm(H))

        def objective(x):
            return np.abs(x - self.w + self.eta * np.matmul(H_inv, loss)).sum()

        constraint = LinearConstraint(self.A, ub=self.b)
        res = minimize(objective, self.w, constraints=constraint)
        self.w = np.clip(res.x, 1e-12, 1 - 1e-12)


class OnlineGradientDescent:
    """
    Online Gradient Descent
    """

    def __init__(self, d, ini_lr=1):
        self.n_arms = d
        self.ini_lr = ini_lr
        self.allocation = np.ones(d) / d
        self.t = 0

    def get_weights(self):
        """
        Get weights
        """
        return self.allocation

    def update(self, loss):
        """
        Update weights
        """
        self.t += 1
        lr = self.ini_lr / np.sqrt(self.t)
        self.allocation -= lr * loss
        self.allocation = project_on_feasible(self.allocation, None, None)





########### BANDIT ENVIRONMENTS ############ß


class Bandit:
    """
    Generic bandit class
    """

    def __init__(self, expected_rewards, expected_constraints, seed=None):
        self.n_arms = len(expected_rewards)
        self.expected_rewards = expected_rewards
        self.expected_constraints = expected_constraints
        self.seed = seed
        self.random_state = np.random.RandomState(seed)

    def sample_mean(self):
        pass

    def get_means(self):
        return self.expected_rewards
    
    def get_constraints(self):
        return self.expected_constraints


class GaussianBandit(Bandit):
    """
    Bandit with gaussian rewards
    """
    def __init__(self, expected_rewards, expected_constraints, seed=None):
        super(GaussianBandit, self).__init__(expected_rewards, expected_constraints, seed)
        #self.noise = noise

    def sample_mean(self):
        return self.random_state.normal(self.expected_rewards,1)
    
    def sample_constraint(self,A):
        B = A.flatten()
        constraints = np.random.normal(B,1)
        return constraints.reshape(A.shape[0],A.shape[1])

class BernoulliBandit(Bandit):
    """
    Bandit with bernoulli rewards
    """

    def __init__(self, expected_rewards, seed=None):
        super(BernoulliBandit, self).__init__(expected_rewards, seed)

    def sample(self):
        return self.random_state.binomial(1, self.expected_rewards)





########### EXPLORATION EXPERIMENT ############

import time

def run_exploration_experiment(bandit, explorer, A, b):
    """
    Run pure-exploration experiment for a given explorer and return stopping time and correctness
    """

    res,_ = get_policy(bandit.get_means(), bandit.get_constraints(), b)
    optimal_policy = res["x"]
    done = False
    t = 0
    running_times = []
    #gram_mat = np.eye(A.shape[1])

    policy_list = []
    done_list = []
    arm_list = []
    constraint_violation = 0
    simple_regret = []
    while not done and t<100000:
        t += 1
        # Act
        running_time = time.time()
        try:
            arm, done, policy, log = explorer.act()
            #print(1)
            #print(policy)
            policy_list.append(policy)
            done_list.append(done)
            arm_list.append(arm)
        except TypeError:
            #print(2)
            policy = policy_list[-1]
            arm = arm_list[-1]
            done = done_list[-1]
        #print(policy)
        if policy is not None:
            simple_regret.append(mu.dot(optimal_policy-policy))
            diff = A.dot(policy) - b
            #print(diff)
            if (diff > 0).sum() > 0:
                constraint_violation+= 1 
        running_time = time.time() - running_time
        running_times.append(running_time)
        # Observe reward
        reward = bandit.sample_mean()[arm]
        constraint_est = bandit.sample_constraint(A)
        cost = np.matmul(constraint_est,np.eye(A.shape[1])[arm])
        #print(cost)
        # Update explorer
        explorer.update(arm, reward, cost)
        #print(policy_list)

    # Check correctness
    correct = np.array_equal(optimal_policy, policy)

    # Return stopping time, correctness, optimal policy and recommended policy
    return t, correct, optimal_policy, policy, np.mean(running_times),constraint_violation,simple_regret


def gaussian_projection_lb(w, mu, pi1, pi2, sigma=1):
    """
    perform close-form projection onto the hyperplane lambda^T(pi1 - pi2) = 0 assuming Gaussian distribution
    :param w: weight vector
    :param mu: reward vector
    :param pi1: optimal policy
    :param pi2: suboptimal neighbor policy
    :param sigma: standard deviation of Gaussian distribution

    return:
        - lambda: projection
        - value of the projection

    """
    v = pi1 - pi2
    normalizer = ((v**2) / (w + PRECISION)).sum()
    lagrange = mu.dot(v) / normalizer
    lam = mu - lagrange * v / (w + PRECISION)
    var = sigma**2
    value = (w * ((mu - lam) ** 2)).sum() / (2 * var)
    return lam, value

def best_response_lb(w, mu, pi, neighbors, sigma=1, dist_type="Gaussian"):
    """
    Compute best response instance w.r.t. w by projecting onto neighbors
    :param w: weight vector
    :param mu: reward vector
    :param pi: optimal policy
    :param neighbors: list of neighbors
    :param sigma: standard deviation of Gaussian distribution
    :param dist_type: distribution type to use for projection

    return:
        - value of best response
        - best response instance
    """
    if dist_type == "Gaussian":
        projections = [
            gaussian_projection_lb(w, mu, pi, neighbor, sigma) for neighbor in neighbors
        ]
    elif dist_type == "Bernoulli":
        projections = [
            bernoulli_projection(w, mu, pi, neighbor, sigma) for neighbor in neighbors
        ]
    else:
        raise NotImplementedError
    values = [p[1] for p in projections]
    instances = [p[0] for p in projections]
    return np.min(values), instances[np.argmin(values)]

def solve_game_lb(
    mu,
    vertex,
    neighbors,
    sigma=1,
    dist_type="Gaussian",
    allocation_A=None,
    allocation_b=None,
    tol=None,
    x0=None,
):
    """
    Solve the game instance w.r.t. reward vector mu. Used for track-n-stop algorithms
    :param mu: reward vector
    :param vertex: vertex of the game
    :param neighbors: list of neighbors
    :param sigma: standard deviation of Gaussian distribution
    :param dist_type: distribution type to use for projection
    :param allocation_A: allocation constraint. If None allocations lies in simplex.
    :param tol: Default None for speed in TnS.
    :param x0: initial point
    """

    def game_objective(w):
        return -best_response_lb(w, mu, vertex, neighbors, sigma, dist_type)[0]

    tol_sweep = [1e-16, 1e-12, 1e-6, 1e-4]  # Avoid tolerance issues in scipy
    if tol is not None and tol > tol_sweep[0]:
        tol_sweep = [tol] + tol_sweep
    else:
        tol_sweep = [None] + tol_sweep  # Auto tune tol via None
    if allocation_A is None:
        # Solve optimization problem over simplex
        simplex = np.ones_like(mu).reshape(1, -1)
        constraint = LinearConstraint(A=simplex, lb=1, ub=1)
        bounds = [(0, 1) for _ in range(len(mu))]
        count = 0
        done = False
        if x0 is None:
            while count < len(tol_sweep) and not done:
                x0 = np.random.uniform(0.3, 0.6, size=len(mu))
                x0 = x0 / x0.sum()
                tol = tol_sweep[count]
                res = minimize(
                    game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
                )
                done = res["success"]
                count += 1
        else:
            res = minimize(
                game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
            )
    else:
        # Solve optimization problem over allocation constraint
        constraint = LinearConstraint(A=allocation_A, ub=allocation_b)
        bounds = [(0, 1) for _ in range(len(mu))]
        count = 0
        done = False
        if x0 is None:
            while count < len(tol_sweep) and not done:
                tol = tol_sweep[count]
                x0 = np.random.uniform(0.3, 0.6, size=len(mu))
                x0 = project_on_feasible(x0, allocation_A, allocation_b)
                res = minimize(
                    game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
                )
                done = res["success"]
                count += 1
        else:
            res = minimize(
                game_objective, x0, constraints=constraint, bounds=bounds, tol=tol
            )
    if res["success"] == False:
        raise ValueError("Optimization failed")
    return res.x, -res.fun



########### TESTING ############
seed = 1000
if __name__ == "__main__":
    iteration = 30
    delta = 0.01
    l0_init = np.random.randn(2)
    bai_lb = []
    known_lb = []
    mean_stop = []
    sd_stop = []
    median_stop = []
    for mu_3 in np.arange(1.3,1.7,0.1):
        mu = np.array([1.5, 1, mu_3, 0.4, 0.3, 0.2])
        A = np.array([[1, 1, 0, 0, 0, 0], [0, 0, 1, 1, 1, 0]])
        b = np.array([0.5, 0.5])
        sol,aux = get_policy(mu, A, b)
        optimal_policy = sol["x"]
        print(f" Optimal : {optimal_policy}")
        hash_tuple = tuple(optimal_policy.tolist())  # npy not hashable
        neighbors = compute_neighbors(
                optimal_policy, aux["A"], aux["b"], slack=aux["slack"])
        allocation, game_value = solve_game_lb(
            mu=mu,
            vertex=optimal_policy,
            neighbors=neighbors,
            dist_type="Gaussian",
            sigma=1.0,
            allocation_A=None,
            allocation_b=None,
        )
        known_lb_value = (1/game_value)*np.log(1/(2.4*delta))
        print(known_lb_value)
        
        known_lb.append(known_lb_value)
        
        simplex = np.ones_like(mu).reshape(1, -1)
        eye = np.eye(len(mu))
        one = np.array([1])
        
        A_1 = np.concatenate([-eye, simplex, -simplex], axis=0)
        b_1 = np.concatenate([np.zeros(len(mu)), one, -one], axis=0)
        sol_1,aux_1 = get_policy(mu, A_1, b_1)
        optimal_policy_1 = sol_1["x"]
        neighbors_1 = compute_neighbors(
                optimal_policy_1, aux["A"], aux["b"], slack=aux["slack"])
        allocation_1, game_value_1 = solve_game_lb(
            mu=mu,
            vertex=optimal_policy_1,
            neighbors=neighbors_1,
            dist_type="Gaussian",
            sigma=1.0,
            allocation_A=None,
            allocation_b=None,
        )
        bai_lb_value = (1/game_value_1)*np.log(1/(2.4*delta))
        bai_lb.append(bai_lb_value)
        print(bai_lb_value)
        
        #mu = np.array([3,2,4])
        #A = np.array([[-3,-2,-1]])
        #b = np.array([-2])
        
        bandit = GaussianBandit(mu,A,seed = seed)
        A_init = bandit.sample_constraint(A)
        stopping_times = []
        
        
        for iter in range(iteration):
            
            explorer = CGE(
                len(mu),
                A_init = A_init,
                b=b,
                delta=delta,
                l0 = l0_init,
                restricted_exploration=True,
                dist_type="Gaussian",
            )
            

            t, correct, _, policy, _, constraint_violation, simple_regret= run_exploration_experiment(bandit, explorer, A, b)
            stopping_times.append(t)

            print(f"Iteration {iter+1} stopped at {t} with recommended policy {policy.round(1)} and constraint is violated {constraint_violation} times")
        
        mean_stop.append(np.mean(np.array(stopping_times)))
        median_stop.append(np.median(np.array(stopping_times)))
        sd_stop.append(np.std(np.array(stopping_times)))
        print(f"Mean stopping time - {np.mean(np.array(stopping_times))}")
        print(f"lower bound in known setting - {known_lb_value}")
        print(f"lower bound in BAI setting - {bai_lb_value}")
    print(mean_stop)
    print(median_stop)
    print(sd_stop)
    print(known_lb)
    print(bai_lb)
        
    
    